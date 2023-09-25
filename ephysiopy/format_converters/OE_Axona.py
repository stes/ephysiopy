import os
from dataclasses import dataclass
from pathlib import Path

import h5py
import numpy as np
from scipy import signal

from ephysiopy.axona import axonaIO
from ephysiopy.common.ephys_generic import PosCalcsGeneric
from ephysiopy.io.recording import OpenEphysBase
from ephysiopy.openephys2py import OESettings


class OE2Axona(object):
    """
    Converts openephys data recorded in the nwb format into Axona files
    """

    def __init__(self, pname: Path, **kwargs):
        '''
        pname: Path
            the base directory of the openephys recording
            e.g. '/home/robin/Data/M4643_2023-07-21_11-52-02'
        '''
        pname = Path(pname)
        assert pname.exists()
        self.pname = pname
        # 'experiment_1.nwb'
        self.experiment_name = Path('experiment_1') or Path(kwargs['experiment_name'])
        self.recording_name = None  # will become 'recording1' etc
        self.OE_data = None  # becomes instance of io.recording.OpenEphysBase
        self._settings = None  # will become an instance of OESettings.Settings
        # Create a basename for Axona file names
        # e.g.'/home/robin/Data/experiment_1'
        # that we can append '.pos' or '.eeg' or whatever onto
        self.axona_root_name = self.pname / self.experiment_name
        # need to instantiated now for later
        self.AxonaData = axonaIO.IO(self.axona_root_name.name + ".pos")
        # THIS IS TEMPORARY AND WILL BE MORE USER-SPECIFIABLE IN THE FUTURE
        # it is used to scale the spikes
        self.hp_gain = 500
        self.lp_gain = 15000
        self.bitvolts = 0.195
        # if left as None some default values for the next 3 params are loaded
        #  from top-level __init__.py
        # these are only used in self.__filterLFP__
        self.fs = None
        # if lfp_channel is set to None then the .set file will reflect that
        #  no EEG was recorded
        # this should mean that you can load data into Tint without a .eeg file
        self.lfp_channel = 1
        self.lfp_lowcut = None
        self.lfp_highcut = None
        # set the tetrodes to record from
        # defaults to 1 through 4 - see self.makeSetData below
        self.tetrodes = ["1", "2", "3", "4"]
        self.channel_count = 0

    def resample(self, data, src_rate=30, dst_rate=50, axis=0):
        """
        Resamples data using FFT
        """
        denom = np.gcd(dst_rate, src_rate)
        new_data = signal.resample_poly(
            data, dst_rate / denom, src_rate / denom, axis)
        return new_data

    @property
    def settings(self):
        """
        Loads the settings data from the settings.xml file
        """
        if self._settings is None:
            self._settings = OESettings.Settings(self.pname)
        return self._settings

    @settings.setter
    def settings(self, value):
        self._settings = value

    def getOEData(
            self,
            filename_root: str,
            recording_name="recording1") -> dict:
        """
        Loads the nwb file names in filename_root and returns a dict
        containing some of the nwb data
        relevant for converting to Axona file formats

        Parameters
        ----------------
        filename_root - fuly qualified name of the nwb file
        recording_name - the name of the recording in the nwb file NB the
        default has changed in different versions of OE from 'recording0'
        to 'recording1'
        """
        if Path(filename_root).exists():
            OE_data = OpenEphysBase(self.pname)
            OE_data.load_pos_data()
            # It's likely that spikes have been collected after the last
            # position sample
            # due to buffering issues I can't be bothered to resolve.
            # Get the last pos
            # timestamps here and check that spikes don't go beyond
            #  this when writing data
            # out later
            # Also the pos and spike data timestamps almost never start at
            #  0 as the user
            # usually acquires data for a while before recording.
            # Grab the first timestamp
            # here with a view to subtracting this from everything (including
            # the spike data)
            # and figuring out what to keep later
            first_pos_ts = OE_data.PosCalcs.xyTS[0]
            last_pos_ts = OE_data.PosCalcs.xyTS[-1]
            self.first_pos_ts = first_pos_ts
            self.last_pos_ts = last_pos_ts
            self.recording_name = recording_name
            self.OE_data = OE_data
            # extract number of channels from settings
            for item in self.settings.record_nodes.items():
                if "Rhythm Data" in item[1].name:
                    self.channel_count = int(item[1].channel_count)
            return OE_data

    def exportSetFile(self, **kwargs):
        """
        Wrapper for makeSetData below
        """
        print("Exporting set file data...")
        self.makeSetData(kwargs)
        print("Done exporting set file.")

    def exportPos(self, ppm=300, jumpmax=100, as_text=False):
        #
        # Step 1) Deal with the position data first:
        #
        # Grab the settings of the pos tracker and do some post-processing
        # on the position
        # data (discard jumpy data, do some smoothing etc)
        self.settings.parse()
        print("Post-processing position data...")
        self.OE_data.PosCalcs.jumpmax = jumpmax
        self.OE_data.PosCalcs.tracker_params["AxonaBadValue"] = 1023
        self.OE_data.PosCalcs.postprocesspos(
            self.OE_data.PosCalcs.tracker_params)
        xy = self.OE_data.PosCalcs.xy.T
        xyTS = self.OE_data.PosCalcs.xyTS
        if as_text is True:
            print("Beginning export of position data to text format...")
            pos_file_name = self.axona_root_name + ".txt"
            np.savetxt(pos_file_name, xy, fmt="%1.u")
            print("Completed export of position data")
            return
        # Do the upsampling of both xy and the timestamps
        print("Beginning export of position data to Axona format...")
        axona_pos_data = self.convertPosData(xy, xyTS)
        # make sure pos data length is same as duration * num_samples
        axona_pos_data = axona_pos_data[
            0: int(self.last_pos_ts - self.first_pos_ts) * 50
        ]
        # Create an empty header for the pos data
        from ephysiopy.axona.file_headers import PosHeader

        pos_header = PosHeader()
        tracker_params = self.OE_data.PosCalcs.tracker_params
        min_xy = np.floor(np.min(xy, 0)).astype(int).data
        max_xy = np.ceil(np.max(xy, 0)).astype(int).data
        pos_header.pos["min_x"] = str(
            tracker_params["LeftBorder"]) if "LeftBorder" in \
            tracker_params.keys() else str(min_xy[0])
        pos_header.pos["min_y"] = str(
            tracker_params["TopBorder"]) if "TopBorder" in \
            tracker_params.keys() else str(max_xy[1])
        pos_header.pos["max_x"] = str(
            tracker_params["RightBorder"]) if "RightBorder" in \
            tracker_params.keys() else str(max_xy[0])
        pos_header.pos["max_y"] = str(
            tracker_params["BottomBorder"]) if "BottomBorder" in \
            tracker_params.keys() else str(min_xy[1])
        pos_header.common["duration"] = str(
            int(self.last_pos_ts - self.first_pos_ts))
        pos_header.pos["pixels_per_metre"] = str(ppm)
        pos_header.pos["num_pos_samples"] = str(len(axona_pos_data))
        pos_header.pos["pixels_per_metre"] = str(ppm)

        self.writePos2AxonaFormat(pos_header, axona_pos_data)
        print("Exported position data to Axona format")

    def exportSpikes(self):
        print("Beginning conversion of spiking data...")
        self.convertSpikeData(
            self.OE_data.nwbData["acquisition"][
                "\
                timeseries"
            ][self.recording_name]["spikes"]
        )
        print("Completed exporting spiking data")

    def exportLFP(self, channel: int=0, lfp_type: str='eeg', gain: int=5000):
        """
        Export LFP data to file

        Parameters
        -----------
        channel - int
        lfp_type - str. Legal values are 'egf' or 'eeg'
        gain - int. Multiplier for the lfp data
        """
        print("Beginning conversion and exporting of LFP data...")
        if not self.settings.processors:
            self.settings.parse()
        if self.OE_data.path2APdata:
            from ephysiopy.io.recording import memmapBinaryFile
            data = memmapBinaryFile(
                Path(self.OE_data.path2APdata) / Path("continuous.dat"),
                n_channels=self.channel_count)
            self.makeLFPData(data[channel, :], eeg_type=lfp_type, gain=gain)
            # if the set file has been created then update which channel
            # contains the eeg record so
            # that the gain can be loaded correctly when using axona_util

            print("Completed exporting LFP data to " + lfp_type + " format")
        else:
            print("Couldn't load raw data")

    def convertPosData(self, xy: np.array, xy_ts: np.array) -> np.array:
        """
        Perform the conversion of the array parts of the data
        NB As well as upsampling the data to the Axona pos sampling rate (50Hz)
        we have to insert some columns into the pos array as Axona format
        expects it like:
        pos_format: t,x1,y1,x2,y2,numpix1,numpix2
        We can make up some of the info and ignore other bits
        """
        n_new_pts = int(np.floor((self.last_pos_ts - self.first_pos_ts) * 50))
        t = xy_ts - self.first_pos_ts
        new_ts = np.linspace(t[0], t[-1], n_new_pts)
        new_x = np.interp(new_ts, t, xy[:, 0])
        new_y = np.interp(new_ts, t, xy[:, 1])
        # Expand the pos bit of the data to make it look like Axona data
        new_pos = np.vstack([new_x, new_y]).T
        new_pos = np.c_[
            new_pos,
            np.ones_like(new_pos) * 1023,
            np.zeros_like(new_pos),
            np.zeros_like(new_pos),
        ]
        new_pos[:, 4] = 40  # just made this value up - it's numpix i think
        new_pos[:, 6] = 40  # same
        # Squeeze this data into Axona pos format array
        dt = self.AxonaData.axona_files[".pos"]
        new_data = np.zeros(n_new_pts, dtype=dt)
        # Timestamps in Axona are time in seconds * sample_rate
        new_data["ts"] = new_ts * 50
        new_data["pos"] = new_pos
        return new_data

    def convertTemplateDataToAxonaTetrode(self, max_n_waves=2000, **kwargs):
        '''
        Converts the data held in a TemplateModel instance into tetrode
        format Axona data files

        Notes
        -----
        For each cluster there'll be a channel that 
        has a peak amplitude and this contains that peak channel.
        Whilst the other channels with a large signal in might be 
        on the same tetrode, KiloSort (or whatever) might find
        channels *not* within the same tetrode. For a given cluster
        we can extract from the TemplateModel the 12 channels across
        which the signal is strongest using Model.get_cluster_channels()
        If a channel from a tetrode is missing from this list then the
        spikes for that channel(s) will be zeroed when saved to Axona
        format. For example, if cluster 3 has a peak channel of 1 then 
        get_cluster_channels() might look like:

            [ 1,  2,  0,  6, 10, 11,  4,  12,  7,  5,  8,  9]

        Here the cluster has the best signal on 1, then 2, 0 etc, but 
        note that channel 3 isn't in the list. In this case the data for
        channel 3 will be zeroed when saved to Axona format.

        References
        -----------

        1) https://phy.readthedocs.io/en/latest/api/#phyappstemplatetemplatemodel)

        '''
        self.OE_data.load_neural_data()  # loads the TemplateModel
        model = self.OE_data.template_model
        clusts = model.cluster_ids
        clust_channels = model.clusters_channels

        for i_tet in range(0, self.channel_count, 4):
            i_tet_clusts = [cl for cl in clusts
                            if clust_channels[cl] < i_tet+4
                            and clust_channels[cl] > i_tet]
            for clust in i_tet_clusts:
                clust_chans = model.get_cluster_channels(clust)
                idx = np.logical_and(clust_chans >= i_tet,
                                     clust_chans < i_tet+4)
                # clust_chans is an ordered list of the channels
                # the cluster was most active on. idx has True
                # where there is overlap between that and the
                # currently active tetrode channel numbers (0:4, 4:8
                # or whatever)
                spike_idx = model.get_cluster_spikes(clust)
                # limit the number of spikes to max_n_waves in the
                # interests of speed. Randomly select spikes across
                # the period they fire
                rng = np.random.default_rng()
                total_n_waves = len(spike_idx)
                max_n_waves = max_n_waves if max_n_waves < total_n_waves else \
                    total_n_waves
                spike_idx_subset = rng.choice(
                    spike_idx, max_n_waves, replace=False)
                ordered_chans = np.argsort(clust_chans[idx])
                waves = model.get_waveforms(spike_idx_subset, ordered_chans)
                # Take the central 30 samples which corresponds to a 1ms sample
                # if the data is sampled at 30kHz. Then interpolate this so the
                # length is 50 samples as with Axona
                waves = waves[:, 26:56, :]
                # note that waves go from int16 to float as a result of the resampling here
                waves = self.resample(waves.astype(float), axis=1)
                # multiply by bitvolts to get microvolts
                waves = waves * self.bitvolts
                # scale appropriately for Axona and invert as
                # OE seems to be inverted wrt Axona
                waves = waves / (self.hp_gain / 4 / 128.0) * (-1)
                # check the shape of waves to make sure it has 4
                # channels, if not add some to make it so and make
                # sure they are in the correct order for the tetrode
                if waves.shape[-1] != 4:
                    z = np.zeros(shape=(waves.shape[0], waves.shape[1], 4))
                    z[:, :, ordered_chans] = waves
                    waves = z
                else:
                    waves = waves[:, :, ordered_chans]
                # Axona format tetrode waveforms are nSpikes x 4 x 50
                waves = np.transpose(waves, (0, 2, 1))
                # times are in seconds
                times = model.spike_times[model.spike_clusters == clust]
                # Axona format times are in sampled at 96kHz



    def convertSpikeData(self, hdf5_tetrode_data: h5py._hl.group.Group):
        """
        Does the spike conversion from OE Spike Sorter format to Axona
        format tetrode files

        Parameters
        -----------
        hdf5_tetrode_data - h5py._hl.group.Group -
            this kind of looks like a dictionary and can, it seems,
            be treated as one more or less.
            See http://docs.h5py.org/en/stable/high/group.html
        """
        # First lets get the datatype for tetrode files as this will be the
        # same for all tetrodes...
        dt = self.AxonaData.axona_files[".1"]
        # ... and a basic header for the tetrode file that use for each
        # tetrode file, changing only the num_spikes value
        from ephysiopy.axona.file_headers import TetrodeHeader

        header = TetrodeHeader()
        header.common["duration"] = str(
            int(self.last_pos_ts - self.first_pos_ts))

        for key in hdf5_tetrode_data.keys():
            spiking_data = np.array(hdf5_tetrode_data[key].get("data"))
            timestamps = np.array(hdf5_tetrode_data[key].get("timestamps"))
            # check if any of the spiking data is captured before/ after the
            #  first/ last bit of position data
            # if there is then discard this as we potentially have no valid
            # position to align the spike to :(
            idx = np.logical_or(
                timestamps < self.first_pos_ts, timestamps > self.last_pos_ts
            )
            spiking_data = spiking_data[~idx, :, :]
            timestamps = timestamps[~idx]
            # subtract the first pos timestamp from the spiking timestamps
            timestamps = timestamps - self.first_pos_ts
            # get the number of spikes here for use below in the header
            num_spikes = len(timestamps)
            # repeat the timestamps in tetrode multiples ready for Axona export
            new_timestamps = np.repeat(timestamps, 4)
            new_spiking_data = spiking_data.astype(np.float64)
            # Convert to microvolts...
            new_spiking_data = new_spiking_data * self.bitvolts
            # And upsample the spikes...
            new_spiking_data = self.resample(new_spiking_data, 4, 5, -1)
            # ... and scale appropriately for Axona and invert as
            # OE seems to be inverted wrt Axona
            new_spiking_data = new_spiking_data / \
                (self.hp_gain / 4 / 128.0) * (-1)
            # ... scale them to the gains specified somewhere
            #  (not sure where / how to do this yet)
            shp = new_spiking_data.shape
            # then reshape them as Axona wants them a bit differently
            new_spiking_data = np.reshape(
                new_spiking_data, [shp[0] * shp[1], shp[2]])
            # Cap any values outside the range of int8
            new_spiking_data[new_spiking_data < -128] = -128
            new_spiking_data[new_spiking_data > 127] = 127
            # create the new array
            new_tetrode_data = np.zeros(len(new_timestamps), dtype=dt)
            new_tetrode_data["ts"] = new_timestamps * 96000
            new_tetrode_data["waveform"] = new_spiking_data
            # change the header num_spikes field
            header.tetrode_entries["num_spikes"] = str(num_spikes)
            i_tetnum = key.split("electrode")[1]
            print("Exporting tetrode {}".format(i_tetnum))
            self.writeTetrodeData(i_tetnum, header, new_tetrode_data)

    def makeLFPData(
                    self,
                    hdf5_continuous_data: np.array,
                    eeg_type="eeg",
                    gain=5000):
        """
        Downsamples the data in hdf5_continuous_data and saves the result
        as either an egf or eeg file depending on the choice of
        either eeg_type which can
        take a value of either 'egf' or 'eeg'
        gain is the scaling factor

        Parameters
        ----------
        hdf5_continuous_data - np.array with dtype as np.int16
        """
        if eeg_type == "eeg":
            from ephysiopy.axona.file_headers import EEGHeader

            header = EEGHeader()
            dst_rate = 250
        elif eeg_type == "egf":
            from ephysiopy.axona.file_headers import EGFHeader

            header = EGFHeader()
            dst_rate = 4800
        header.common["duration"] = str(
            int(self.last_pos_ts - self.first_pos_ts))

        lfp_data = self.resample(hdf5_continuous_data, 30000, dst_rate, -1)
        # make sure data is same length as sample_rate * duration
        nsamples = int(dst_rate * int(header.common["duration"]))
        lfp_data = lfp_data[0:nsamples]
        lfp_data = self.__filterLFP__(lfp_data, dst_rate)
        # convert the data format
        # lfp_data = lfp_data * self.bitvolts # in microvolts

        if eeg_type == "eeg":
            # probably BROKEN
            # lfp_data starts out as int16 (see Parameters above)
            lfp_data = lfp_data / 32768.0
            lfp_data = lfp_data * gain
            # cap the values at either end...
            lfp_data[lfp_data < -128] = -128
            lfp_data[lfp_data > 127] = 127
            # and convert to int8
            lfp_data = lfp_data.astype(np.int8)

        elif eeg_type == "egf":
            # probably works
            # lfp_data = lfp_data / 256.
            lfp_data = lfp_data.astype(np.int16)

        header.n_samples = str(len(lfp_data))
        self.writeLFP2AxonaFormat(header, lfp_data, eeg_type)

    def makeSetData(self, lfp_channel=1, **kwargs):
        if self.OE_data is None:
            # to get the timestamps for duration key
            self.getOEData(self.filename_root)
        from ephysiopy.axona.file_headers import SetHeader

        header = SetHeader()
        # set some reasonable default values
        from ephysiopy.__about__ import __version__

        header.sw_version = __version__
        header.ADC_fullscale_mv = "0.195"
        header.tracker_version = "1.1.0"

        for k, v in header.set_entries.items():
            if "gain" in k:
                header.set_entries[k] = str(self.hp_gain)
            if "collectMask" in k:
                header.set_entries[k] = "0"
            if "EEG_ch_1" in k:
                if lfp_channel is not None:
                    header.set_entries[k] = str(lfp_channel)
            if "mode_ch_" in k:
                header.set_entries[k] = "0"
        # iterate again to make sure lfp gain set correctly
        for k, v in header.set_entries.items():
            if lfp_channel is not None:
                if k == "gain_ch_" + str(lfp_channel):
                    header.set_entries[k] = str(self.lp_gain)

        # Based on the data in the electrodes dict of the OESettings
        # instance (self.settings - see __init__)
        # determine which tetrodes we can let Tint load
        # make sure we've parsed the electrodes
        tetrode_count = int(self.channel_count / 4)
        for i in range(1, tetrode_count+1):
            header.set_entries["collectMask_" + str(i)] = "1"
        if self.lfp_channel is not None:
            for chan in self.tetrodes:
                key = "collectMask_" + str(chan)
                header.set_entries[key] = "1"
        header.set_entries["colactive_1"] = "1"
        header.set_entries["colactive_2"] = "0"
        header.set_entries["colactive_3"] = "0"
        header.set_entries["colactive_4"] = "0"
        header.set_entries["colmap_algorithm"] = "1"
        header.set_entries["duration"] = str(
            int(self.last_pos_ts - self.first_pos_ts))
        self.writeSetData(header)

    def __filterLFP__(self, data: np.array, sample_rate: int):
        from scipy.signal import filtfilt, firwin

        if self.fs is None:
            from ephysiopy import fs

            self.fs = fs
        if self.lfp_lowcut is None:
            from ephysiopy import lfp_lowcut

            self.lfp_lowcut = lfp_lowcut
        if self.lfp_highcut is None:
            from ephysiopy import lfp_highcut

            self.lfp_highcut = lfp_highcut
        nyq = sample_rate / 2.0
        lowcut = self.lfp_lowcut / nyq
        highcut = self.lfp_highcut / nyq
        if highcut >= 1.0:
            highcut = 1.0 - np.finfo(float).eps
        if lowcut <= 0.0:
            lowcut = np.finfo(float).eps
        b = firwin(sample_rate + 1, [lowcut, highcut],
                   window="black", pass_zero=False)
        y = filtfilt(b, [1], data.ravel(), padtype="odd")
        return y

    def writeLFP2AxonaFormat(
                             self,
                             header: dataclass,
                             data: np.array,
                             eeg_type="eeg"):
        self.AxonaData.setHeader(self.axona_root_name.name + "." + eeg_type, header)
        self.AxonaData.setData(self.axona_root_name.name + "." + eeg_type, data)

    def writePos2AxonaFormat(self, header: dataclass, data: np.array):
        self.AxonaData.setHeader(self.axona_root_name.name + ".pos", header)
        self.AxonaData.setData(self.axona_root_name.name + ".pos", data)

    def writeTetrodeData(self, tetnum: str, header: dataclass, data: np.array):
        self.AxonaData.setHeader(self.axona_root_name.name + "." + tetnum, header)
        self.AxonaData.setData(self.axona_root_name.name + "." + tetnum, data)

    def writeSetData(self, header: dataclass):
        self.AxonaData.setHeader(self.axona_root_name.name + ".set", header)
