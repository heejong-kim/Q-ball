
from qball.sphere import load_sphere
from qball.tools import normalize_odf
from qball.tools.diff import staggered_diff_avgskips
from qball.solvers.sh_w_tvw.pd import pd_iteration_step, compute_primal_obj, compute_dual_obj
import qball.util as util

import numpy as np

import logging

def qball_regularization(f, gtab, sampling_matrix,
        lbd=10.0,
        term_relgap=1e-7,
        term_infeas=None,
        term_maxiter=20000,
        step_bound=0.08,
        step_factor=0.001,
        granularity=5000,
        use_gpu=True,
        constraint_u=None,
        dataterm="W1",
        continue_at=None
    ):
    """ Solve ...

    Args:
        f : reference image
        gtab : bvals and bvecs
        ... : more keyword arguments
    Returns:
        pd_state : the solution; a tuple of numpy arrays
                        (uk, vk, wk, w0k, pk, gk, q0k, q1k, p0k, g0k)
                   that can be put back into this function as the `continue_at`
                   parameter.
        details : dictionary containing information on the objective primal
                  and dual functions (including feasibility and pd-gap).
    """
    b_vecs = gtab.bvecs[gtab.bvals > 0,...].T
    b_sph = load_sphere(vecs=b_vecs)

    imagedims = f.shape[:-1]
    n_image = np.prod(imagedims)
    d_image = len(imagedims)
    l_labels = b_sph.mdims['l_labels']
    s_manifold = b_sph.mdims['s_manifold']
    m_gradients = b_sph.mdims['m_gradients']
    r_points = b_sph.mdims['r_points']
    assert(f.shape[-1] == l_labels)

    f = np.array(f.reshape(-1, l_labels).T.reshape((l_labels,) + imagedims), order='C')
    normalize_odf(f, b_sph.b)

    Y = np.zeros(sampling_matrix.shape, order='C')
    Y[:] = sampling_matrix
    l_shm = Y.shape[1]

    logging.info("Solving ({l_labels} labels, {l_shm} shm, m={m}; img: {imagedims}; " \
        "dataterm: {dataterm}, lambda={lbd:.3g}, steps<{maxiter})...".format(
        lbd=lbd,
        m=m_gradients,
        l_labels=l_labels,
        l_shm=l_shm,
        imagedims="x".join(map(str,imagedims)),
        dataterm=dataterm,
        maxiter=term_maxiter
    ))

    if term_infeas is None:
        term_infeas = term_relgap

    if constraint_u is None:
        constraint_u = np.zeros((l_labels,) + imagedims, order='C')
        constraint_u[:] = np.nan
    uconstrloc = np.any(np.logical_not(np.isnan(constraint_u)), axis=0)

    bnd = step_bound   # < 1/|K|^2
    fact = step_factor # tau/sigma
    sigma = np.sqrt(bnd/fact)
    tau = bnd/sigma
    theta = 0.99

    obj_p = obj_d = infeas_p = infeas_d = relgap = 0.

    if continue_at is None:
        # start with a uniform distribution in each voxel
        uk = np.ones((l_labels,) + imagedims, order='C')/np.einsum('k->', b_sph.b)
        uk[:,uconstrloc] = constraint_u[:,uconstrloc]

        vk = np.zeros((l_shm, n_image), order='C')
        vk[0,:] = .5 / np.sqrt(np.pi)
        wk = np.zeros((n_image, m_gradients, s_manifold, d_image), order='C')
        w0k = np.zeros((n_image, m_gradients, s_manifold), order='C')
        pk = np.zeros((l_labels, d_image, n_image), order='C')
        gk = np.zeros((n_image, m_gradients, s_manifold, d_image), order='C')
        q0k = np.zeros(n_image)
        q1k = np.zeros((l_labels, n_image), order='C')
        p0k = np.zeros((l_labels, n_image), order='C')
        g0k = np.zeros((n_image, m_gradients, s_manifold), order='C')
    else:
        uk, vk, wk, w0k, pk, gk, q0k, q1k, p0k, g0k = (ar.copy() for ar in continue_at)

    ukp1 = uk.copy()
    vkp1 = vk.copy()
    wkp1 = wk.copy()
    w0kp1 = w0k.copy()
    pkp1 = pk.copy()
    gkp1 = gk.copy()
    q0kp1 = q0k.copy()
    q1kp1 = q1k.copy()
    p0kp1 = p0k.copy()
    g0kp1 = g0k.copy()
    ubark = uk.copy()
    vbark = vk.copy()
    wbark = wk.copy()
    w0bark = w0k.copy()
    g_norms = np.zeros((n_image, m_gradients), order='C')

    avgskips = staggered_diff_avgskips(imagedims)

    if dataterm == "quadratic":
        dataterm_factor = 1.0/(1.0 + tau*b_sph.b)
    elif dataterm == "W1":
        dataterm_factor = np.ones((l_labels,))
    else:
        raise Exception("Dataterm '%s' not supported!" % dataterm)

    constvars = {
        'Y': Y,
        'f': f,
        'constraint_u': constraint_u,
        'uconstrloc': uconstrloc,
        'sigma': sigma,
        'tau': tau,
        'theta': theta,
        'lbd': lbd,
        'avgskips': avgskips,
        'dataterm': dataterm,
    }

    itervars = {
         'uk': uk,
         'vk': vk,
         'wk': wk,
         'w0k': w0k,
         'ukp1': ukp1,
         'vkp1': vkp1,
         'wkp1': wkp1,
         'w0kp1': w0kp1,
         'ubark': ubark,
         'vbark': vbark,
         'wbark': wbark,
         'w0bark': w0bark,
         'pk': pk,
         'gk': gk,
         'q0k': q0k,
         'q1k': q1k,
         'p0k': p0k,
         'g0k': g0k,
         'pkp1': pkp1,
         'gkp1': gkp1,
         'q0kp1': q0kp1,
         'q1kp1': q1kp1,
         'p0kp1': p0kp1,
         'g0kp1': g0kp1
    }

    extravars = {
        'dataterm_factor': dataterm_factor,
        'g_norms': g_norms,
        'b_sph': b_sph
    }

    if use_gpu:
        from qball.tools.cuda import prepare_kernels, iterate_on_gpu
        skips = (1,)
        for d in reversed(imagedims[1:]):
            skips += (skips[-1]*d,)
        gpu_constvars = dict(constvars, **{
            'b': b_sph.b,
            'A': b_sph.A,
            'B': b_sph.B,
            'P': b_sph.P,
            'b_precond': b_sph.b_precond,
            'imagedims': np.array(imagedims, dtype=np.int64, order='C'),
            'l_labels': l_labels,
            'n_image': n_image,
            'm_gradients': m_gradients,
            's_manifold': s_manifold,
            'd_image': d_image,
            'r_points': r_points,
            'l_shm': l_shm,
            'navgskips': 1 << (d_image - 1),
            'skips': np.array(skips, dtype=np.int64, order='C'),
            'dataterm': dataterm[0].upper(),
            'nd_skip': d_image*n_image,
            'ld_skip': d_image*l_labels,
            'sd_skip': s_manifold*d_image,
            'ss_skip': s_manifold*s_manifold,
            'sr_skip': s_manifold*r_points,
            'sm_skip': s_manifold*m_gradients,
            'msd_skip': m_gradients*s_manifold*d_image,
            'ndl_skip': n_image*d_image*l_labels,
        })
        cuda_templates = [
            ("DualKernel1", (s_manifold*m_gradients, n_image, d_image), (16, 16, 1)),
            ("DualKernel2", (l_labels, n_image, d_image), (16, 16, 1)),
            ("DualKernel3", (n_image, m_gradients, 1), (16, 16, 1)),
            ("PrimalKernel1", (l_labels, 1, 1), (16, 1, 1)),
            ("PrimalKernel2", (s_manifold*m_gradients, n_image, d_image), (16, 16, 1)),
            ("PrimalKernel3", (n_image, l_labels, 1), (16, 16, 1)),
            ("PrimalKernel4", (n_image, l_shm, 1), (16, 16, 1)),
        ]
        from pkg_resources import resource_stream
        cuda_files = [
            resource_stream('qball.solvers.sh_w_tvw', 'cuda_primal.cu'),
            resource_stream('qball.solvers.sh_w_tvw', 'cuda_dual.cu'),
        ]
        cuda_kernels, cuda_vars = prepare_kernels(cuda_files, cuda_templates,
                                                  gpu_constvars, itervars)

    with util.GracefulInterruptHandler() as interrupt_hdl:
        _iter = 0
        while _iter < term_maxiter:
            if use_gpu:
                iterations = iterate_on_gpu(cuda_kernels, cuda_vars, granularity)
                _iter += iterations
                if iterations < granularity:
                    interrupt_hdl.handle(None, None)
            else:
                pd_iteration_step(**itervars, **constvars, **extravars)
                _iter += 1

            if interrupt_hdl.interrupted or _iter % granularity == 0:
                obj_p, infeas_p = compute_primal_obj(**itervars, **constvars, **extravars)
                obj_d, infeas_d = compute_dual_obj(**itervars, **constvars, **extravars)

                # compute relative primal-dual gap
                relgap = (obj_p - obj_d) / max(np.spacing(1), obj_d)

                logging.debug("#{:6d}: objp = {: 9.6g} ({: 9.6g}), " \
                    "objd = {: 9.6g} ({: 9.6g}), " \
                    "gap = {: 9.6g}, " \
                    "relgap = {: 9.6g} ".format(
                    _iter, obj_p, infeas_p,
                    obj_d, infeas_d,
                    obj_p - obj_d,
                    relgap
                ))

                if relgap < term_relgap and max(infeas_p, infeas_d) < term_infeas:
                    break

                if interrupt_hdl.interrupted:
                    break

    return (uk, vk, wk, w0k, pk, gk, q0k, q1k, p0k, g0k), {
        'objp': obj_p,
        'objd': obj_d,
        'infeasp': infeas_p,
        'infeasd': infeas_d,
        'relgap': relgap
    }
