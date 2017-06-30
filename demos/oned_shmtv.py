
"""
    This standalone application applies several reconstruction themes to a 1d
    Q-ball data set, i.e. the input image is an ODF in each pixel.
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
from qball.models import OuyangModel

class MyExperiment(util.QBallExperiment):
    Model = OuyangModel

    def __init__(self, args):
        util.QBallExperiment.__init__(self, "1d-shmtv", args)
        #self.params['fit'] = {
        #    'solver_engine': 'cvx',
        #    'solver_params': { 'lbd': 5.0, },
        #}
        self.params['fit'] = {
            'solver_engine': 'pd',
            'solver_params': {
                'lbd': 5.0,
                'term_relgap': 1e-05,
                'term_maxiter': 100000,
                'granularity': 10000,
                'step_factor': 0.1,
                'step_bound': 0.0014,
                'use_gpu': True
            },
        }

    def setup_imagedata(self):
        logging.info("Data setup.")
        #np.random.seed(seed=234234)
        self.S_data_orig, self.S_data, self.gtab = gen.synth_unimodals()

if __name__ == "__main__":
    logging.info("Running from command line: %s" % sys.argv)
    exp = MyExperiment(sys.argv[1:])
    exp.run()
    exp.plot()
