
from qball.tools.blocks import BlockVar
from qball.tools.norm import norms_frobenius, project_duals
from qball.tools.diff import gradient, divergence
from qball.solvers import PDHGModelHARDI_SHM

import numpy as np
from numpy.linalg import norm

import logging

def fit_hardi_qball(data, model_params, solver_params):
    solver = MyPDHGModel(data, model_params)
    details = solver.solve(**solver_params)
    return solver.state, details

class MyPDHGModel(PDHGModelHARDI_SHM):
    def __init__(self, *args):
        PDHGModelHARDI_SHM.__init__(self, *args)

        c = self.constvars
        e = self.extravars
        i = self.itervars

        imagedims = c['imagedims']
        n_image = c['n_image']
        d_image = c['d_image']
        l_labels = c['l_labels']
        l_shm = c['l_shm']

        e['p_norms'] = np.zeros((n_image,), order='C')

        i['xk'] = BlockVar(
            ('u1', (l_labels,) + imagedims),
            ('u2', (l_labels, n_image)),
            ('v', (l_shm, n_image)),
        )
        i['yk'] = BlockVar(
            ('p', (l_shm, d_image, n_image)),
            ('q0', (n_image,)),
            ('q1', (l_labels, n_image)),
            ('q2', (l_labels, n_image)),
        )

        # start with a uniform distribution in each voxel
        u1k = i['xk']['u1']
        u1k[:] = 1.0/np.einsum('k->', c['b'])
        u1k[:,c['uconstrloc']] = c['constraint_u'][:,c['uconstrloc']]

        vk = i['xk']['v']
        vk[0,:] = .5 / np.sqrt(np.pi)

    def prepare_gpu(self):
        c = self.constvars
        n_image = c['n_image']
        d_image = c['d_image']
        l_labels = c['l_labels']
        s_manifold = c['s_manifold']
        m_gradients = c['m_gradients']
        l_shm = c['l_shm']

        prox_sg= "PP" if 'precond' in c else "Pd"
        self.cuda_templates = [
            ("prox_primal", prox_sg, (n_image, l_labels, 1), (16, 16, 1)),
            ("prox_dual", prox_sg, (n_image, 1, 1), (512, 1, 1)),
            ("linop1", "PP", (l_shm, n_image, d_image), (16, 16, 1)),
            ("linop2", "PP", (l_labels, n_image, d_image), (16, 16, 1)),
            ("linop_adjoint1", "PP", (l_labels, 1, 1), (512, 1, 1)),
            ("linop_adjoint2", "PP", (n_image, l_labels, l_shm), (16, 8, 2)),
        ]

        from pkg_resources import resource_stream
        self.cuda_files = [
            resource_stream('qball.solvers.sh_l_tvc', 'cuda_primal.cu'),
            resource_stream('qball.solvers.sh_l_tvc', 'cuda_dual.cu'),
        ]

        PDHGModelHARDI_SHM.prepare_gpu(self)

        def gpu_kernels_linop(*args):
            self.cuda_kernels['linop1'](*args)
            self.cuda_kernels['linop2'](*args)
        self.gpu_kernels['linop'] = gpu_kernels_linop

        def gpu_kernels_linop_adjoint(*args):
            self.cuda_kernels['linop_adjoint1'](*args)
            self.cuda_kernels['linop_adjoint2'](*args)
        self.gpu_kernels['linop_adjoint'] = gpu_kernels_linop_adjoint

        self.gpu_kernels['prox_primal'] = self.cuda_kernels['prox_primal']
        self.gpu_kernels['prox_dual'] = self.cuda_kernels['prox_dual']

    def obj_primal(self, x, ygrad):
        # ygrad is precomputed via self.linop(x, ygrad)
        #   pgrad = Dv
        #   q0grad = b'u1
        #   q1grad = Yv - u1
        #   q2grad = YMv - u2
        u1, u2, v = x.vars()
        pgrad, q0grad, q1grad, q2grad = ygrad.vars()
        c = self.constvars
        e = self.extravars

        norms_frobenius(pgrad, e['p_norms'])

        # obj_p = 0.5*<u2-f,u2-f>_b + lbd*\sum_i |(Dv)^i|_2
        umf_sq = 0.5*(u2 - c['f'])**2
        obj_p = np.einsum('k,ki->', c['b'], umf_sq[:,c['inpaint_nloc']]) \
              + c['lbd']*e['p_norms'].sum()

        # infeas_p = |b'u1 - 1| + |Yv - u1| + |YMv - u2| + |max(0,-u1)|
        infeas_p = norm(q0grad.ravel() - c['b_precond'], ord=np.inf) \
            + norm(q1grad.ravel(), ord=np.inf) \
            + norm(q2grad[:,c['inpaint_nloc']].ravel(), ord=np.inf) \
            + norm(np.fmax(0.0, -u1.ravel()), ord=np.inf)

        return obj_p, infeas_p

    def obj_dual(self, xgrad, y):
        # xgrad is precomputed via self.linop_adjoint(xgrad, y)
        #   u1grad = b q0' - q1
        #   u2grad = -q2
        #   vgrad = Y'q1 + M Y'q2 + D' p
        p, q0, q1, q2 = y.vars()
        u1grad, u2grad, vgrad = xgrad.vars()
        c = self.constvars
        e = self.extravars
        l_labels = u1grad.shape[0]

        # obj_d = -\sum_i q0_i + 0.5*b*[f^2 - (diag(1/b) q2 + f)^2]
        f_flat = c['f'][:,c['inpaint_nloc']]
        u2tmp = u2grad[:,c['inpaint_nloc']] - np.einsum('k,ki->ki', c['b'], f_flat)
        obj_d = -np.sum(q0)*c['b_precond'] \
              + np.einsum('k,ki->', 0.5*c['b'], f_flat**2) \
              - np.einsum('k,ki->', 0.5/c['b'], u2tmp**2)

        # infeas_d = |Y'q1 + M Y'q2 + D' p| + |max(0, |p| - lbd)|
        #          + |max(0, -b q0' + q1)|
        norms_frobenius(p, e['p_norms'])
        infeas_d = norm(vgrad.ravel(), ord=np.inf) \
                + norm(np.fmax(0, e['p_norms'] - c['lbd']), ord=np.inf) \
                + norm(np.fmax(0.0, -u1grad.ravel()), ord=np.inf)

        return obj_d, infeas_d

    def precond(self, x, y):
        u1, u2, v = x.vars()
        p, q0, q1, q2 = y.vars()
        u1_flat = u1.reshape(u1.shape[0], -1)
        vtmp = v.reshape((v.shape[0],) + u1.shape[1:])
        c = self.constvars
        x[:] = 0.0
        y[:] = 0.0

        # p += D v (D is the gradient on a staggered grid)
        # q0 = b'u1
        # q1 = Yv - u1
        # q2 = YMv - u2
        gradient(p, vtmp, np.ones(v.shape[0]), c['avgskips'], precond=True)
        q0 += c['b_precond']*norm(c['b'], ord=1)
        q1 += norm(c['Y'], ord=1, axis=1)[:,None] + 1.0
        q2[:,c['inpaint_nloc']] += norm(np.einsum('km,m->km', c['Y'], c['M']),
                                        ord=1, axis=1)[:,None] + 1.0
        y[y.data > np.spacing(1)] = 1.0/y[y.data > np.spacing(1)]

        # u1 = b q0' - q1
        # u2 = -q2
        # v = Y'q1 + M Y'q2
        # v += D' p (where D' = -div with Dirichlet boundary)
        u1_flat += c['b_precond']*np.abs(c['b'])[:,None] + 1.0
        u2[:,c['inpaint_nloc']] += 1.0
        v += norm(c['Y'], ord=1, axis=0)[:,None]
        v[:,c['inpaint_nloc']] += norm(np.einsum('m,km->km', c['M'], c['Y']),
                                       ord=1, axis=0)[:,None]
        divergence(p, vtmp, np.ones(v.shape[0]), c['avgskips'], precond=True)
        x[x.data > np.spacing(1)] = 1.0/x[x.data > np.spacing(1)]

    def linop(self, x, ygrad):
        """ Apply the linear operator in the model to x.

        Args:
            x : primal variable
            ygrad : dual target variable
        Returns:
            nothing, the result is written to the given `ygrad`.
        """
        u1, u2, v = x.vars()
        pgrad, q0grad, q1grad, q2grad = ygrad.vars()
        c = self.constvars

        l_labels = u1.shape[0]
        imagedims = u1.shape[1:]
        l_shm, d_image, n_image  = pgrad.shape

        pgrad[:] = 0

        # pgrad += D v (D is the gradient on a staggered grid)
        vtmp = v.reshape((l_shm,) + imagedims)
        gradient(pgrad, vtmp, np.ones(l_shm), c['avgskips'])

        # q0grad = b'u1
        np.einsum('k,ki->i', c['b'], u1.reshape(l_labels, n_image), out=q0grad)
        q0grad *= c['b_precond']

        # q1grad = Yv - u1
        np.einsum('km,mi->ki', c['Y'], v, out=q1grad)
        q1grad -= u1.reshape(l_labels, n_image)

        # q2grad = YMv - u2
        q2grad[:,c['inpaint_nloc']] = np.einsum('km,mi->ki', c['Y'],
            np.einsum('m,mi->mi', c['M'], v[:,c['inpaint_nloc']]))
        q2grad[:,c['inpaint_nloc']] -= u2[:,c['inpaint_nloc']]

    def linop_adjoint(self, xgrad, y):
        """ Apply the adjoint linear operator in the model to y

        Args:
            xgrad : primal target variable
            y : dual variable
        Returns:
            nothing, the result is written to the given `xgrad`.
        """
        u1grad, u2grad, vgrad = xgrad.vars()
        p, q0, q1, q2 = y.vars()
        c = self.constvars

        l_labels = u1grad.shape[0]
        imagedims = u1grad.shape[1:]
        l_shm, d_image, n_image  = p.shape

        # u1grad = b q0' - q1
        u1grad_flat = u1grad.reshape(l_labels, -1)
        np.einsum('k,i->ki', c['b'], c['b_precond']*q0, out=u1grad_flat)
        u1grad_flat -= q1

        # u2grad = -q2
        u2grad[:,c['inpaint_nloc']] = -q2[:,c['inpaint_nloc']]

        # vgrad = Y'q1 + M Y'q2
        np.einsum('km,ki->mi', c['Y'], q1, out=vgrad)
        vgrad[:,c['inpaint_nloc']] += np.einsum('m,mi->mi', c['M'],
            np.einsum('km,ki->mi', c['Y'], q2[:,c['inpaint_nloc']]))

        # vgrad += D' p (where D' = -div with Dirichlet boundary)
        vtmp = vgrad.reshape((l_shm,)+imagedims)
        divergence(p, vtmp, np.ones(l_shm), c['avgskips'])

    def prox_primal(self, x, tau):
        u1, u2, v = x.vars()
        c = self.constvars

        u1[:] = np.fmax(0.0, u1)
        u1[:,c['uconstrloc']] = c['constraint_u'][:,c['uconstrloc']]

        u2tau = tau['u2'][:,c['inpaint_nloc']] if 'precond' in c else tau
        f_flat = c['f'][:,c['inpaint_nloc']]
        u2[:,c['inpaint_nloc']] += u2tau*np.einsum('k,ki->ki', c['b'], f_flat)
        u2[:,c['inpaint_nloc']] *= 1.0/(1.0 + u2tau*c['b'][:,None])

    def prox_dual(self, y, sigma):
        p, q0, q1, q2 = y.vars()
        c = self.constvars
        e = self.extravars

        project_duals(p, c['lbd'], e['p_norms'])

        q0sigma = sigma['q0'] if 'precond' in c else sigma
        q0 -= q0sigma*c['b_precond']
