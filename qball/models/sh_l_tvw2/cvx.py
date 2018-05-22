
from qball.tools.blocks import BlockVar
from qball.tools.cvx import cvxVariable, sparse_div_op, cvxOp
from qball.tools.shm import sym_shm_sample_grad

import numpy as np
import cvxpy as cvx

import logging

def fit_hardi_qball(data, model_params, solver_params={}):
    sampling_matrix = model_params['sampling_matrix']
    model_matrix = model_params['model_matrix']
    lbd = model_params.get('lbd', 1.0)
    data_ext = data
    data = data_ext['raw'][data_ext['slice']]
    b_sph = data_ext['b_sph']

    imagedims = data.shape[:-1]
    n_image = np.prod(imagedims)
    d_image = len(imagedims)
    s_manifold = 2
    l_labels = b_sph.mdims['l_labels']
    m_gradients = b_sph.mdims['m_gradients']
    assert(data.shape[-1] == l_labels)

    f = np.zeros((l_labels, n_image), order='C')
    f[:] = np.log(-np.log(data)).reshape(-1, l_labels).T
    f_mean = np.einsum('ki,k->i', f, b_sph.b)/(4*np.pi)
    f -= f_mean

    Y = np.zeros(sampling_matrix.shape, order='C')
    Y[:] = sampling_matrix
    l_shm = Y.shape[1]

    G = np.zeros((m_gradients,s_manifold,l_shm), order='C')
    G[:] = sym_shm_sample_grad(Y, b_sph.v_grad)

    M = model_matrix
    assert(M.size == l_shm)

    logging.info("Solving ({l_labels} labels, {l_shm} shm, m={m}; " \
                 "img: {imagedims}; lambda={lbd:.3g}) using CVX...".format(
        lbd=lbd,
        m=m_gradients,
        l_labels=l_labels,
        l_shm=l_shm,
        imagedims="x".join(map(str,imagedims)),
    ))

    p  = cvxVariable(l_shm, d_image, n_image)
    g  = cvxVariable(n_image, m_gradients, s_manifold, d_image)
    q0 = cvxVariable(n_image)
    q1 = cvxVariable(l_labels, n_image)
    q2 = cvxVariable(l_labels, n_image)

    obj = cvx.Maximize(
          0.5*cvx.sum_entries(cvx.diag(b_sph.b)*cvx.square(f))
        - 0.5*cvx.sum_entries(
            cvx.diag(1.0/b_sph.b)*cvx.square(q2 + cvx.diag(b_sph.b)*f)
        ) - cvx.sum_entries(q0)
    )

    div_op = sparse_div_op(imagedims)

    constraints = []
    for i in range(n_image):
        for j in range(m_gradients):
            constraints.append(cvx.norm(g[i][j], 2) <= lbd)

    w_constr = []
    for j in range(m_gradients):
        Gj = G[j,:,:]
        for i in range(n_image):
            for t in range(d_image):
                w_constr.append(
                    g[i][j][:,t] == sum([Gj[:,k]*p[k,t,i] for k in range(l_shm)])
                )
    constraints += w_constr

    u1_constr = []
    for k in range(l_labels):
        for i in range(n_image):
            u1_constr.append(
               b_sph.b[k]*q0[i] - q1[k,i] >= 0
            )
    constraints += u1_constr

    v_constr = []
    for k in range(l_shm):
        for i in range(n_image):
            Yk = cvx.vec(Y[:,k])
            v_constr.append(
                Yk.T*(M[k]*q2[:,i] + q1[:,i]) == cvxOp(div_op, p[k], i)
            )
    constraints += v_constr

    prob = cvx.Problem(obj, constraints)
    prob.solve(verbose=False)

    # Store result in block variables
    x = BlockVar(
        ('u1', (l_labels,) + imagedims),
        ('u2', (l_labels, n_image)),
        ('v', (l_shm, n_image)),
        ('w', (n_image, m_gradients, s_manifold, d_image)),
    )

    y = BlockVar(
        ('p', (l_shm, d_image, n_image)),
        ('g', (n_image, m_gradients, s_manifold, d_image)),
        ('q0', (n_image,)),
        ('q1', (l_labels, n_image)),
        ('q2', (l_labels, n_image)),
    )

    for k in range(l_labels):
        y['p'][k,:] = p[k].value

    for i in range(n_image):
        for j in range(m_gradients):
            y['g'][i,j,:,:] = g[i][j].value

    y['q0'][:] = q0.value.ravel()
    y['q1'][:,:] = q1.value
    y['q1'][:,:] = q2.value

    for k in range(l_shm):
        for i in range(n_image):
            x['v'][k,i] = v_constr[k*n_image+i].dual_value

    u1_flat = x['u1'].reshape((l_labels,-1))
    for k in range(l_labels):
        for i in range(n_image):
            u1_flat[k,i] = u1_constr[k*n_image+i].dual_value

    np.einsum('km,mi->ki', sampling_matrix,
        np.einsum('m,mi->mi', model_matrix, x['v']), out=x['u2'])

    for j in range(m_gradients):
        for i in range(n_image):
            for t in range(d_image):
                x['w'][i,j,:,t] = w_constr[(j*n_image + i)*d_image + t].dual_value.ravel()

    logging.info("{}: objd = {: 9.6g}".format(prob.status, prob.value))
    return (x,y), { 'objp': prob.value, 'status': prob.status }