
"""
    This standalone application applies several reconstruction themes to a 1d
    HARDI data set.
"""

from __future__ import division

import logging, sys
import numpy as np

try:
    import qball
except:
    import set_qball_path
import qball.util as util
import qball.tools.gen as gen

fit_params = {
    'n_w_tvw': {
        'sphere': None,
        'solver_params': {
            'lbd': 2.5,
            'term_relgap': 1e-05,
            'term_maxiter': 100000,
            'granularity': 5000,
            'step_factor': 0.0001,
            'step_bound': 1.2, # 0.993
            'dataterm': "W1",
            'use_gpu': True
        },
    },
    'sh_w_tvw': {
        'solver_engine': 'pd',
        'solver_params': {
            'lbd': 10.0,
            'term_relgap': 1e-05,
            'term_maxiter': 100000,
            'granularity': 5000,
            'step_factor': 0.001,
            'step_bound': 0.08, # 0.0651
            'dataterm': "W1",
            'use_gpu': True
        },
    },
    'sh_l_tvc': {
        'solver_engine': 'pd',
        'solver_params': {
            'lbd': 5.0,
            'term_relgap': 1e-05,
            'term_maxiter': 100000,
            'granularity': 10000,
            'step_factor': 0.1,
            'step_bound': 0.0013, # 0.00104
            'use_gpu': True
        },
    },
    'sh_l_tvo': {
        'solver_engine': 'pd',
        'solver_params': {
            'lbd': 1.0,
            'term_relgap': 1e-05,
            'term_maxiter': 100000,
            'granularity': 10000,
            'step_factor': 0.29,
            'step_bound': 0.0014, # 0.00105
            'use_gpu': True
        },
    },
    'sh_l_tvw': {
        'solver_engine': 'pd',
        'solver_params': {
            'lbd': 1.0,
            'term_relgap': 1e-05,
            'term_maxiter': 150000,
            'granularity': 5000,
            'step_factor': 0.01,
            'step_bound': 0.0014, # 0.00105
            'use_gpu': True
        },
    },
}

class MyExperiment(util.QBallExperiment):
    def __init__(self, args):
        util.QBallExperiment.__init__(self, "1d", args)
        if not self.cvx:
            self.params['fit'] = fit_params[self.model_name]

    def setup_imagedata(self):
        logging.info("Data setup.")
        #np.random.seed(seed=234234)
        self.S_data_orig, self.S_data, self.gtab = gen.synth_unimodals()

if __name__ == "__main__":
    logging.info("Running from command line: %s" % sys.argv)
    exp = MyExperiment(sys.argv[1:])
    exp.run()
    exp.plot()
