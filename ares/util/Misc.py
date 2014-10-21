"""

Misc.py

Author: Jordan Mirocha
Affiliation: University of Colorado at Boulder
Created on: Sun Oct 19 19:50:31 MDT 2014

Description: 

"""

import re
import numpy as np
from collections import Iterable
from .ProblemTypes import ProblemType
from .SetDefaultParameterValues import SetAllDefaults
from .CheckForParameterConflicts import CheckForParameterConflicts

try:
    from mpi4py import MPI
    rank = MPI.COMM_WORLD.rank
    size = MPI.COMM_WORLD.size
except ImportError:
    rank = 0
    size = 1

defaults = SetAllDefaults()

logbx = lambda b, x: np.log10(x) / np.log10(b)

def parse_kwargs(**kwargs):
    """
    Parse kwargs dictionary - populate with defaults.
    """    
    
    pf = defaults.copy()
    
    if not kwargs:
        pf.update(ProblemType(1))
    elif 'problem_type' in kwargs:
        pf.update(ProblemType(kwargs['problem_type']))
    
    # Count populations
    popIDs = [0]
    for par in kwargs:
        
        m = re.search(r"\{([0-9])\}", par)
        
        if m is None:
            continue
                    
        num = int(m.group(1))

        if num not in popIDs:
            popIDs.append(num)
            
    Npops = len(popIDs)
        
    if Npops == 1:
        pf.update(kwargs)
    else:
        src_kw = [{} for i in range(Npops)]
        spec_kw = [{} for i in range(Npops)]
        
        # Construct parameter file
        for par in kwargs:
            
            m = re.search(r"\{([0-9])\}", par)
            
            if m is None:
                pf[par] = kwargs[par]
                continue
            
            num = int(m.group(1))
            
            prefix = par.strip(m.group(0))
                        
            if re.search('spectrum', par):
                spec_kw[num][prefix] = kwargs[par]
            else:
                src_kw[num][prefix] = kwargs[par]

        pf.update({'source_kwargs': src_kw})
        pf.update({'spectrum_kwargs': spec_kw})
    
    # Check for unrecognizable parameters and (known) conflicts        
    for kwarg in pf:
        if kwarg not in defaults.keys():
            if rank != 0:
                continue
            print 'WARNING: Unrecognized parameter: %s' % kwarg        
        
    conflicts = CheckForParameterConflicts(pf)

    if conflicts:
        raise Exception('Conflict(s) in input parameters.')

    return pf
    
class evolve:
    """ Make things that may or may not evolve with time callable. """
    def __init__(self, val):
        self.val = val
        self.callable = val == types.FunctionType
    def __call__(self, z = None):
        if self.callable:
            return self.val(z)
        else:
            return self.val
            
def sort(pf, prefix='spectrum', make_list=True, make_array=False):
    """
    Turn any item that starts with prefix_ into a list, if it isn't already.
    Hack off the prefix when we're done.
    """            

    result = {}
    for par in pf.keys():
        if par[0:len(prefix)] != prefix:
            continue

        new_name = par.partition('_')[-1]
        if (isinstance(pf[par], Iterable) and type(pf[par]) is not str) \
            or (not make_list):
            result[new_name] = pf[par]
        elif make_list:
            result[new_name] = [pf[par]]

    # Make sure all elements are the same length?      
    if make_list or make_array:  
        lmax = 1
        for par in result:
            lmax = max(lmax, len(result[par]))

        for par in result:
            if len(result[par]) == lmax:
                continue

            result[par] = lmax * [result[par][0]]

            if make_array:
                result[par] = np.array(result[par])

    return result

def num_freq_bins(Nx, zi=40, zf=10, Emin=2e2, Emax=3e4):
    """
    Compute number of frequency bins required for given log-x grid.

    Defining the variable x = 1 + z, and setting up a grid in log-x containing
    Nx elements, compute the number of frequency bins required for a 1:1 
    mapping between redshift and frequency.

    """
    x = np.logspace(np.log10(1.+zf), np.log10(1.+zi), Nx)
    R = x[1] / x[0]

    # Create mapping to frequency space
    Etmp = 1. * Emin
    n = 1
    while Etmp < Emax:
        Etmp = Emin * R**(n - 1)
        n += 1

    # Subtract 2: 1 because we overshoot Emax in while loop, another because
    # n is index-1-based (?)

    return n-2

