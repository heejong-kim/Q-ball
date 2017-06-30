
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
from qball.models import SSVMModel

class MyExperiment(util.QBallExperiment):
    Model = SSVMModel

    def __init__(self, args):
        util.QBallExperiment.__init__(self, "1d-ssvm", args)
        self.params['fit'] = {
            'sphere': None,
            'solver_params': {
                'lbd': 2.5,
                'term_relgap': 1e-05,
                'term_maxiter': 100000,
                'granularity': 5000,
                'step_factor': 0.0001,
                'step_bound': 1.3,
                'dataterm': "W1",
                'use_gpu': True
            },
        }

    def solve(self):
        self.params['fit']['sphere'] = self.qball_sphere
        util.QBallExperiment.solve(self)

    def setup_imagedata(self):
        logging.info("Data setup.")
        #np.random.seed(seed=234234)
        self.S_data_orig, self.S_data, self.gtab = gen.synth_unimodals()

if __name__ == "__main__":
    logging.info("Running from command line: %s" % sys.argv)
    exp = MyExperiment(sys.argv[1:])
    exp.run()
    exp.plot()
