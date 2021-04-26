import pytest

from ephysiopy.common.binning import RateMap
import numpy as np


@pytest.fixture
def standard_Ratemap(basic_PosCalcs):
    '''Returns a Ratemap instance with a random walk as x,y'''
    P = basic_PosCalcs
    xy, hdir = P.postprocesspos()
    # only have 10 seconds of spiking data so limit the pos stuff to that too
    P.xy = P.xy[:, 0:10*P.sample_rate]
    P.dir = P.dir[0:10*P.sample_rate]
    P.speed = P.speed[0:10*P.sample_rate]
    P.npos = 10*P.sample_rate
    return RateMap(P.xy, P.dir, P.speed)


def test_pixels_per_bin(standard_Ratemap):
    ppb = standard_Ratemap.pixelsPerBin
    assert(isinstance(ppb, (int, float)))


def test_calc_bin_size(standard_Ratemap):
    bs = standard_Ratemap.__calcBinSize__()
    assert(isinstance(bs, np.ndarray))


def test_blur_image(basic_ratemap, standard_Ratemap):
    filt = ['box', 'gaussian']
    rmap1D = basic_ratemap[0, :]
    rmap2D = basic_ratemap
    rmap3D = np.atleast_3d(rmap2D)
    rmaps = [rmap1D, rmap2D, rmap3D]
    for f in filt:
        for rmap in rmaps:
            b = standard_Ratemap.blurImage(rmap, 3, ftype=f)
            assert(isinstance(b, np.ndarray))


def test_bin_data(standard_Ratemap):
    R = standard_Ratemap
    xy = getattr(R, 'xy')
    xy_bins = R.binsize
    hd = getattr(R, 'dir')
    R.inCms
    R.inCms = True
    hd_bins = np.arange(0, 360+R.cmsPerBin, R.cmsPerBin)
    samples = [xy, hd]
    bins = [xy_bins, hd_bins]
    for sample in zip(samples, bins):
        ret = R.__binData__(sample[0], sample[1], R.pos_weights)
        assert(isinstance(ret, np.ndarray))


def test_get_map(standard_Ratemap):
    # A large number of the methods in RateMap are
    # called within the method getMap()
    n_pos = len(standard_Ratemap.pos_weights)
    spk_weights = np.random.rand(n_pos)
    spk_weights[spk_weights >= 0.95] = 1
    spk_weights[spk_weights >= 0.99] = 2
    spk_weights[spk_weights >= 0.99] = 3
    spk_weights[spk_weights < 0.95] = 0

    vars_2_bin = ['xy', 'dir', 'speed']
    map_types = ['rate', 'pos']
    smoothing_when = ['after', 'before']
    do_smooth = [True, False]
    # There is a member variable to smooth before or after dividing
    # binned spikes by the spatial variable that needs to be set in the
    # iteration below

    for var in vars_2_bin:
        for map_type in map_types:
            for smooth in do_smooth:
                for when2smooth in smoothing_when:
                    standard_Ratemap.whenToSmooth = when2smooth
                    ret = standard_Ratemap.getMap(
                        spk_weights, varType=var, mapType=map_type,
                        smoothing=smooth)
                    assert(isinstance(ret[0], np.ndarray))


def test_get_adaptive_map(standard_Ratemap):
    n_pos = len(standard_Ratemap.pos_weights)
    spk_weights = np.random.rand(n_pos)
    spk_weights[spk_weights >= 0.95] = 1
    spk_weights[spk_weights >= 0.99] = 2
    spk_weights[spk_weights >= 0.99] = 3
    spk_weights[spk_weights < 0.95] = 0

    rmap = standard_Ratemap.getMap(
        spk_weights)
    pos_binned, _ = standard_Ratemap.getMap(
        spk_weights, mapType='pos')
    pos_binned[~np.isfinite(pos_binned)] = 0
    smthdRate, smthdSpk, smthdPos = standard_Ratemap.getAdaptiveMap(
        rmap[0], pos_binned)
    assert(isinstance(smthdRate, np.ndarray))


def test_auto_corr_2D(basic_ratemap, standard_Ratemap):
    nodwell = ~np.isfinite(basic_ratemap)
    SAC = standard_Ratemap.autoCorr2D(basic_ratemap, nodwell)
    assert(isinstance(SAC, np.ndarray))


def test_cross_corr_2D(basic_ratemap, standard_Ratemap):
    A = basic_ratemap
    B = np.rot90(np.rot90(A))
    A_dwell = ~np.isfinite(A)
    B_dwell = ~np.isfinite(B)
    cc = standard_Ratemap.crossCorr2D(A, B, A_dwell, B_dwell)
    assert(isinstance(cc, np.ndarray))


def test_t_win_SAC(basic_xy, standard_Ratemap):
    x, y = basic_xy
    xy = np.array([x, y])
    t = np.random.rand(xy.shape[1])
    spk_idx = np.nonzero(t > 0.95)[0]
    H = standard_Ratemap.tWinSAC(xy, spk_idx)
    assert(isinstance(H, np.ndarray))
