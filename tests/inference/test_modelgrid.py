"""

test_modelgrid.py

Author: Jordan Mirocha
Affiliation: University of Colorado at Boulder
Created on: Sun Jun 15 12:59:04 MDT 2014

Description: 

"""

import ares, time
import numpy as np
import matplotlib.pyplot as pl

#
##
fstar = 10**np.arange(-1.0, -0.5, 0.1)
fX = 10**np.arange(-1., 1.5, 0.1)
grid_axes = {'fstar': fstar, 'fX': fX}
##
#

blob_names = \
    ['igm_Tk', 'cgm_Gamma_h_1', 'cgm_h_2', 'Ts', 'Ja', 'curvature',
     'z', 'dTb', 'tau_e', 'sfrd']

blob_z = ['B', 'C', 'D', 'trans', 'eor_midpt', 'eor_overlap',
    6, 7, 8, 10, 12, 15, 20, 30]

blobs = (blob_names, blob_z)

base_kwargs = {'inline_analysis': blobs, 'final_redshift': 6}

mg = ares.inference.ModelGrid(**base_kwargs)

mg.set_axes(**grid_axes)

t1 = time.time()
mg.run('test_grid', clobber=True)
t2 = time.time()

print "Run complete in %.4g minutes.\n" % ((t2 - t1) / 60.)





