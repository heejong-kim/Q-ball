
import os, itertools, warnings
import numpy as np
import matplotlib.collections

import nibabel as nib
from dipy.core.gradients import GradientTable, gradient_table
from dipy.sims.voxel import multi_tensor
from dipy.sims.phantom import add_noise
from dipy.data import fetch_stanford_hardi, read_stanford_hardi
from dipy.data.fetcher import _make_fetcher, dipy_home
from dipy.io.gradients import read_bvals_bvecs

# median filter -> otsu -> mask (bg blacked out)
from dipy.segment.mask import median_otsu

from qball.sphere import load_sphere

try:
    from contextlib import redirect_stdout
except ImportError:
    # shim for Python 2.x
    import sys
    from contextlib import contextmanager
    @contextmanager
    def redirect_stdout(new_target):
        old_target, sys.stdout = sys.stdout, new_target # replace sys.stdout
        try:
            yield new_target # run some code with the replaced stdout
        finally:
            sys.stdout = old_target # restore to the previous value

# 64 directions
# 50x50x50 voxels
fetch_isbi2013_challenge = _make_fetcher(
    "fetch_isbi2013_challenge",
    os.path.join(dipy_home, 'isbi2013_challenge'),
    'http://hardi.epfl.ch/static/events/2013_ISBI/_downloads/',
    [
        'testing-data_DWIS_hardi-scheme_SNR-10.nii.gz',
        'testing-data_DWIS_hardi-scheme_SNR-20.nii.gz',
        'testing-data_DWIS_hardi-scheme_SNR-30.nii.gz',
        'hardi-scheme.bval', 'hardi-scheme.bvec',
    ],
    [
        'hardi-scheme_SNR-10.nii.gz',
        'hardi-scheme_SNR-20.nii.gz',
        'hardi-scheme_SNR-30.nii.gz',
        'hardi-scheme.bval', 'hardi-scheme.bvec',
    ],
    [
        'c3d97559f418358bb69467a0b5809630',
        '33640b1297c8b498e0328fe268dbd5c1',
        'a508716c5eec555a77a34817acafb0ca',
        '92811d6e800a6a56d7498b0c4b5ed0c2',
        'c8f5025b9d91037edb6cd00af9bd3e41',
    ])

def read_isbi2013_challenge(snr=30):
    """ Load ISBI 2013's HARDI reconstruction challenge dataset

    Returns
    -------
        img : obj, Nifti1Image
        gtab : obj, GradientTable
    """
    files, folder = fetch_isbi2013_challenge()
    fraw = os.path.join(folder, 'hardi-scheme_SNR-%d.nii.gz' % snr)
    fbval = os.path.join(folder, 'hardi-scheme.bval')
    fbvec = os.path.join(folder, 'hardi-scheme.bvec')
    bvals, bvecs = read_bvals_bvecs(fbval, fbvec)
    gtab = gradient_table(bvals, bvecs)
    img = nib.load(fraw)
    return img, gtab

def synth_isbi2013(snr=30):
    supported_snrs = np.array([10,20,30])
    snr = supported_snrs[np.argmin(np.abs(supported_snrs - snr))]
    with redirect_stdout(open(os.devnull, "w")), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        img, gtab = read_isbi2013_challenge(snr=snr)
        assert(img.shape[-1] == gtab.bvals.size)
        data = img.get_data()
    S_data = np.array(data[12:27,22,21:36], order='C')
    return S_data.copy(), S_data, gtab

def rw_stanford(snr=None):
    with redirect_stdout(open(os.devnull, "w")), warnings.catch_warnings():
        warnings.simplefilter("ignore")
        img, gtab = read_stanford_hardi()
        assert(img.shape[-1] == gtab.bvals.size)
        data = img.get_data()
        maskdata, mask = median_otsu(data, 3, 1, True,
                                     vol_idx=range(10, 50), dilate=2)

    S_data = np.array(maskdata[13:43, 44:74, 28], order='C')
    S_data_orig = S_data.copy()
    if snr is not None:
        S_data[:] = add_noise(S_data_orig, snr=snr)
    return S_data_orig, S_data, gtab

def one_fiber_signal(gtab, angle, snr=None):
    mevals = np.array([[1500e-6, 300e-6, 300e-6]])
    signal, sticks = multi_tensor(gtab, mevals,
        S0=1., angles=[(90,angle)], fractions=[100], snr=snr)
    return signal

def synth_unimodals(bval=3000, imagedims=(8,), jiggle=10, snr=None):
    d_image = len(imagedims)
    n_image = np.prod(imagedims)

    sph = load_sphere(refinement=2)
    l_labels = sph.mdims['l_labels']
    gtab = GradientTable(bval * sph.v.T, b0_threshold=0)

    S_data_orig = np.stack([one_fiber_signal(gtab, 0, snr=None)]*n_image) \
                    .reshape(imagedims + (l_labels,))

    S_data = np.stack([
        one_fiber_signal(gtab, 0+r, snr=snr)
        for r in jiggle*np.random.randn(n_image)
    ]).reshape(imagedims + (l_labels,))

    return S_data_orig, S_data, gtab

def synth_cross(res=15, snr=None):
    # ==========================================================================
    #    Fiber phantom preparation
    # ==========================================================================
    f1 = lambda x: 0.5*(x + 0.3)**3 + 0.05
    f1inv = lambda y: (y/0.5)**(1/3) - 0.3
    f2 = lambda x: 0.7*(1.5 - x)**3 - 0.5
    f2inv = lambda y: 1.5 - ((y + 0.5)/0.7)**(1/3)

    p = FiberPhantom(res)
    p.add_curve(lambda t: (t,f1(t)), tmin=-0.2, tmax=f1inv(1.0)+0.2)
    p.add_curve(lambda t: (t,f2(t)), tmin=f2inv(1.0)-0.2, tmax=f2inv(0.0)+0.2)

    gtab, S_data = p.gen_hardi(snr=snr)
    _, S_data_orig = p.gen_hardi(snr=None)

    return S_data_orig, S_data, gtab, p

def seg_normal(p1, p2):
    p1 = np.array(p1)
    p2 = np.array(p2)
    v = p2 - p1
    n = np.array([-v[1], v[0]])
    n /= np.sum(n**2)**0.5
    return n

def translate_segs(segs, delta, crop=None):
    p = np.array(segs[0]) + delta*seg_normal(segs[0], segs[1])
    result = [tuple(p)]
    for i in range(1,len(segs)-1):
        p = np.array(segs[i]) + delta*seg_normal(segs[i-1], segs[i+1])
        result.append(tuple(p))
    p = np.array(segs[-1]) + delta*seg_normal(segs[-2], segs[-1])
    result.append(tuple(p))
    if crop is not None:
        crop_segs(result, crop[0], crop[1])
    return result

def seg_cropped(seg, cropmin, cropmax):
    tol = 1e-6
    p1 = np.array(seg[0])
    p2 = np.array(seg[1])
    v = p2 - p1
    t1 = np.zeros(p1.size)
    t2 = np.ones(p1.size)
    for i in range(p1.size):
        if np.abs(v[i]) < tol:
            if p1[i] < cropmin[i] or p1[i] > cropmax[i]:
                return None
        else:
            t1[i] = (cropmin[i] - p1[i])/v[i]
            t2[i] = (cropmax[i] - p1[i])/v[i]
            if t1[i] > t2[i]:
                t1[i], t2[i] = t2[i], t1[i]
    t1 = np.amax(np.fmin(1.0, np.fmax(0.0, t1)))
    t2 = np.amin(np.fmin(1.0, np.fmax(0.0, t2)))
    if t2-t1 < tol:
        return None
    return (tuple(p1 + t1*v), tuple(p1 + t2*v))

def crop_segs(segs, cropmin, cropmax):
    """ crops segs in place """
    cropmin = np.array(cropmin)
    cropmax = np.array(cropmax)
    i = 0
    while i < len(segs)-1:
        s = seg_cropped((segs[i], segs[i+1]), cropmin, cropmax)
        if s is None:
            if i == 0:
                del segs[0]
            else:
                del segs[i+1]
        else:
            segs[i], segs[i+1] = s
            i += 1

def compute_dirs(lines, res):
    dirs = np.zeros((res,res,2))
    d = 1.0/res
    for l in lines:
        for (x,y) in itertools.product(range(res), repeat=2):
            cropmin = (d*x,d*y)
            cropmax = (d*(x+1),d*(y+1))
            for i in range(len(l)-1):
                s = seg_cropped((l[i],l[i+1]), cropmin, cropmax)
                if s is not None:
                    dirs[x,y,:] -= s[1]
                    dirs[x,y,:] += s[0]
    dir_norm = np.amax(np.sum(dirs**2, axis=2)**0.5)
    dirs *= 1.0/dir_norm
    return dirs

class FiberPhantom(object):
    def __init__(self, res):
        self.res = res
        self.delta = 1.0/res
        self.curves = []

    def add_curve(self, c, tmin=0.0, tmax=1.0, n=20):
        delta_t = (tmax - tmin)/n
        segs = [c(tmin + i*delta_t) for i in range(n+1)]
        lines = [
            translate_segs(segs, 0.0 + 0.03*d, crop=((0.0,0.0),(1.0,1.0))) \
            for d in range(7)
        ]
        dirs = compute_dirs(lines, self.res)
        self.curves.append({
            'segs': segs,
            'lines': lines,
            'dirs': dirs
        })

    def plot_curves(self, ax):
        lines = [l for c in self.curves for l in c['lines']]
        lc = matplotlib.collections.LineCollection(lines)
        ax.add_collection(lc)

    def plot_grid(self, ax):
        gridlines = [ [(self.delta*x,0.0),(self.delta*x,1.0)] for x in range(1,self.res)]
        gridlines += [ [(0.0,self.delta*y),(1.0,self.delta*y)] for y in range(1,self.res)]
        lc = matplotlib.collections.LineCollection(gridlines, colors=[(0.0,0.0,0.0,0.3)])
        ax.add_collection(lc)

    def plot_dirs(self, ax):
        dir_scaling = 0.07
        for c in self.curves:
            dirs = c['dirs']
            for (x,y) in itertools.product(range(self.res), repeat=2):
                mid = self.delta*(np.array([x,y]) + 0.5)
                ax.scatter(mid[0], mid[1], s=3, c='r', linewidths=0)
                if np.sum(dirs[x,y,:]**2)**0.5 > 1e-6:
                    data = np.array([
                        mid[:] - 0.5*dir_scaling*dirs[x,y,:],
                        mid[:] + 0.5*dir_scaling*dirs[x,y,:]
                    ]).T
                    ax.plot(data[0], data[1], 'r')

    def plot_phantom(self, output_file=None):
        import matplotlib.pyplot as plt
        fig = plt.figure(figsize=(15,5))

        subplot_opts = {
            'aspect': 'equal',
            'xticklabels': [],
            'yticklabels': [],
            'xticks': [],
            'yticks': [],
            'xlim': [0.0,1.0],
            'ylim': [0.0,1.0],
        }

        ax = fig.add_subplot(131, **subplot_opts)
        self.plot_curves(ax)
        ax = fig.add_subplot(132, **subplot_opts)
        self.plot_curves(ax)
        self.plot_grid(ax)
        ax = fig.add_subplot(133, **subplot_opts)
        self.plot_dirs(ax)

        plt.subplots_adjust(left=0.02, bottom=0.02, right=0.98, top=0.98,
            wspace=0.03, hspace=0)

        if output_file is None:
            plt.show()
        else:
            plt.savefig(output_file)

    def gen_hardi(self, snr=20):
        bval = 3000
        sph = load_sphere(refinement=2)
        gtab = GradientTable(bval * sph.v.T, b0_threshold=0)
        l_labels = gtab.bvecs.shape[0]
        val_base = 1e-6*300
        S_data = np.zeros((self.res, self.res, l_labels), order='C')
        for (x,y) in itertools.product(range(self.res), repeat=2):
            mid = self.delta*(np.array([x,y]) + 0.5)
            norms = [np.sum(c['dirs'][x,y,:]**2)**0.5 for c in self.curves]
            if sum(norms) < 1e-6:
                mevals = np.array([[val_base, val_base, val_base]])
                sticks = np.array([[1,0,0]])
                fracs = [100]
            else:
                fracs = 100.0*np.array(norms)/sum(norms)
                mevals = np.array([
                    [(1.0+norm*4.0)*val_base, val_base, val_base]
                    for norm in norms
                ])
                sticks = np.array([
                    np.array([c['dirs'][x,y,0], c['dirs'][x,y,1], 0])/norm
                    if norm > 1e-6 else np.array([1,0,0])
                    for c, norm in zip(self.curves, norms)
                ])
            signal, _ = multi_tensor(gtab, mevals,
                S0=1., angles=sticks, fractions=fracs, snr=snr)
            S_data[x,y,:] = signal
        return gtab, S_data
