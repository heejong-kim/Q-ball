
from qball.tools.blocks import BlockVar
from qball.models.base import ModelHARDI_SHM

import numpy as np

import logging

from opymize.functionals import SplitSum, SSD, PositivityFct, \
                                ZeroFct, IndicatorFct, L1Norms
from opymize.linear import BlockOp, MatrixMultR, ScaleOp, GradientOp

def fit_hardi_qball(data, model_params, solver_params):
    solver = MyModel(data, model_params)
    details = solver.solve(**solver_params)
    return solver.state, details

class MyModel(ModelHARDI_SHM):
    def __init__(self, *args):
        ModelHARDI_SHM.__init__(self, *args)

        c = self.constvars
        e = self.extravars

        imagedims = c['imagedims']
        n_image = c['n_image']
        d_image = c['d_image']
        l_labels = c['l_labels']
        l_shm = c['l_shm']

        self.x = BlockVar(
            ('u1', (n_image, l_labels)),
            ('u2', (n_image, l_labels)),
            ('v', (n_image, l_shm)),
        )

        self.y = BlockVar(
            ('p', (n_image, d_image, l_shm)),
            ('q0', (n_image,)),
            ('q1', (n_image, l_labels)),
            ('q2', (n_image, l_labels)),
        )

        # start with a uniform distribution in each voxel
        u1k, u2k, vk = self.x.vars()
        u1k[:] = 1.0/np.einsum('k->', e['b_sph'].b)
        vk[:,0] = .5 / np.sqrt(np.pi)

        N_u = self.x['u1'].size
        N_v = self.x['v'].size
        N_q0 = self.y['q0'].size

        dataterm = SSD(c['f'], vol=e['b_sph'].b, mask=c['inpaint_nloc'])

        self.G = SplitSum([
            PositivityFct(N_u), # \delta_{u1 >= 0}
            dataterm,           # 0.5*<u2-f,u2-f>
            ZeroFct(N_v)        # 0
        ])

        GradOp = GradientOp(imagedims, l_shm)

        bMult = MatrixMultR(n_image, e['b_sph'].b_precond*e['b_sph'].b[:,None])
        YMult = MatrixMultR(n_image, c['Y'], trans=True)
        YMMult = MatrixMultR(n_image, c['YM'], trans=True)

        m_u = ScaleOp(N_u, -1)

        self.linop = BlockOp([
            [    0,   0, GradOp],   # p  = Dv
            [bMult,   0,      0],   # q0 = <b,u1>
            [  m_u,   0,  YMult],   # q1 = Yv - u1
            [    0, m_u, YMMult]    # q2 = YMv - u2
        ])

        l1norms = L1Norms(n_image, (d_image, l_shm), c['lbd'], "frobenius")

        self.F = SplitSum([
            l1norms,                                # c['lbd']*\sum_i |p[i,:,:]|_2
            IndicatorFct(N_q0, c1=e['b_sph'].b_precond), # \delta_{q0 = 1}
            IndicatorFct(N_u),                      # \delta_{q1 = 0}
            IndicatorFct(N_u)                       # \delta_{q2 = 0}
        ])
