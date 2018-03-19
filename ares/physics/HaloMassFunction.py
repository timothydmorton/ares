""" 
Halo.py

Author: Jordan Mirocha
Affiliation: University of Colorado at Boulder
Created on 2010-03-01.

Description: 

"""

import glob
import pickle
import os, re, sys
import numpy as np
from . import Cosmology
from types import FunctionType
from ..util import ParameterFile
from scipy.misc import derivative
from scipy.optimize import fsolve
from ..util.Misc import get_hg_rev
from ..util.Warnings import no_hmf
from scipy.integrate import cumtrapz
from ..util.PrintInfo import print_hmf
from ..util.ProgressBar import ProgressBar
from ..util.ParameterFile import ParameterFile
from ..util.Math import central_difference, smooth
from .Constants import g_per_msun, cm_per_mpc, s_per_yr, G, cm_per_kpc, m_H, k_B
from scipy.interpolate import UnivariateSpline, RectBivariateSpline, interp1d
    
try:
    from scipy.special import erfc
except ImportError:
    pass
    
try:
    import h5py
except ImportError:
    pass
    
try:
    from mpi4py import MPI
    rank = MPI.COMM_WORLD.rank
    size = MPI.COMM_WORLD.size
except ImportError:
    rank = 0
    size = 1

try:
    from hmf import MassFunction
    have_hmf = True
except ImportError:
    have_hmf = False
    
try:
    import pycamb
    have_pycamb = True
except ImportError:
    have_pycamb = False

ARES = os.getenv("ARES")    

sqrt2 = np.sqrt(2.)    

tiny_fcoll = 1e-18
tiny_dfcolldz = 1e-18

class HaloMassFunction(object):
    def __init__(self, **kwargs):
        """
        Initialize HaloDensity object.
        
        If an input table is supplied, set up interpolation tables over 
        mass and redshift for the collapsed fraction and its derivative.
        If no table is supplied, create one using Steven Murray's halo
        mass function calculator.
        
        =================================
        The following kwargs are relevant
        =================================
        logMmin : float
            Minimum log-Mass value over which to tabulate mass function.
        logMmax : float
            Maximum log-Mass value over which to tabulate mass function.
        dlogM : float
            log-Mass resolution of mass function table.
        zmin : float
            Minimum redshift in mass function table.
        zmax : float
            Maximum redshift in mass function table.
        dz : float
            Redshift resolution in mass function table.
        hmf_func : str
            Halo mass function fitting function. Options are:
                PS
                ST
                Warren
                Jenkins
                Reed03
                Reed07
                Angulo
                Angulo_Bound
                Tinker
                Watson_FoF
                Watson
                Crocce
                Courtin
        hmf_table : str
            HDF5 or binary file containing fcoll table.  
        hmf_analytic : bool
            If hmf_func == 'PS', will compute fcoll analytically.
            Used as a check of numerical calculation.
        compute_dndM : bool
        compute_MgtM : bool
        compute_NgtM : bool    
            
        Table Format
        ------------
        If HDF5, must have datasets z, logM, and fcoll. fcoll table should 
        have shape [len(z), len(logM)].
        If binary, should contain arrays z, logM, and a 2-D spline for fcoll 
        (which accepts arguments z and logM), in that order.
        
        """
        self.pf = ParameterFile(**kwargs)
        
        # Read in a few parameters for convenience        
        self.fn = self.pf["hmf_table"]
        self.hmf_func = self.pf['hmf_model']
        self.hmf_analytic = self.pf['hmf_analytic']
        
        # Verify that Tmax is set correctly
        if self.pf['pop_Tmax'] is not None:
            if self.pf['pop_Tmin'] is not None and self.pf['pop_Mmin'] is None:
                assert self.pf['pop_Tmax'] > self.pf['pop_Tmin'], \
                    "Tmax must exceed Tmin!"
                
        # Look for tables in input directory
        if ARES is not None and self.pf['hmf_load'] and (self.fn is None):
            prefix = self.table_prefix(True)
            fn = '%s/input/hmf/%s' % (ARES, prefix)
            # First, look for a perfect match
            if os.path.exists('%s.%s' % (fn, self.pf['preferred_format'])):
                self.fn = '%s.%s' % (fn, self.pf['preferred_format'])
            # Next, look for same table different format
            elif os.path.exists('%s.pkl' % fn):
                self.fn = '%s.pkl' % fn
            elif os.path.exists('%s.hdf5' % fn):
                self.fn = '%s.hdf5' % fn   
            elif os.path.exists('%s.npz' % fn):
                self.fn = '%s.npz' % fn    
            else:
                # Leave resolution blank, but enforce ranges
                prefix = self.table_prefix()
                candidates = glob.glob('%s/input/hmf/%s*' % (ARES, prefix))

                if len(candidates) == 1:
                    self.fn = candidates[0]
                else:
                    
                    # What parameter file says we need.
                    logMmax = self.pf['hmf_logMmax']
                    logMmin = self.pf['hmf_logMmin']
                    logMsize = (logMmax - logMmin) / self.pf['hmf_dlogM']
                    # Get an extra bin so any derivatives will still be
                    # sane at the boundary.
                    zmax = self.pf['hmf_zmax']
                    zmin = self.pf['hmf_zmin']
                    zsize = (zmax - zmin) / self.pf['hmf_dz'] + 1
                    
                    self.fn = None
                    for candidate in candidates:
                        _Nm, _logMmin, _logMmax, _Nz, _zmin, _zmax = \
                            map(int, re.findall(r'\d+', candidate))
                    
                        if (_logMmin > logMmin) or (_logMmax < logMmax):
                            continue
                            
                        if (_zmin > zmin) or (_zmax < zmax):
                            continue
                            
                        self.fn = candidate    
                            
        # Override switch: compute Press-Schechter function analytically
        if self.hmf_func == 'PS' and self.hmf_analytic:
            self.fn = None
        
        # Either create table from scratch or load one if we found a match
        if self.fn is None:
            if have_hmf and have_pycamb:
                pass
            else:
                no_hmf(self)
                sys.exit()
        #else:
        #    self.load_hmf()
        
        self._is_loaded = False
            
        if self.pf['hmf_dfcolldz_smooth']:
            assert self.pf['hmf_dfcolldz_smooth'] % 2 != 0, \
                'hmf_dfcolldz_smooth must be odd!'
                        
    @property
    def Mmax_ceil(self):
        if not hasattr(self, '_Mmax_ceil'):
            self._Mmax_ceil= 1e18
        return self._Mmax_ceil
    
    @property
    def logMmax_ceil(self):
        if not hasattr(self, '_logMmax'):
            self._logMmax_ceil = np.log10(self.Mmax_ceil)
        return self._logMmax_ceil
               
    @property
    def cosm(self):
        if not hasattr(self, '_cosm'):
            self._cosm = Cosmology(
                omega_m_0=self.pf['omega_m_0'], 
                omega_l_0=self.pf['omega_l_0'], 
                omega_b_0=self.pf['omega_b_0'],  
                hubble_0=self.pf['hubble_0'],  
                helium_by_number=self.pf['helium_by_number'],
                cmb_temp_0=self.pf['cmb_temp_0'],
                approx_highz=self.pf['approx_highz'], 
                sigma_8=self.pf['sigma_8'],
                primordial_index=self.pf['primordial_index'])        

        return self._cosm
            
    def __getattr__(self, name):
        
        if (name[0] == '_'):
            raise AttributeError('This will get caught. Don\'t worry!')
            
        if name not in self.__dict__.keys():
            if self.pf['hmf_load']:
                self.load_hmf()

        return self.__dict__[name]

    def load_hmf(self):
        """ Load table from HDF5 or binary. """
                  
        if self._is_loaded:
            return     
                            
        if re.search('.hdf5', self.fn) or re.search('.h5', self.fn):
            f = h5py.File(self.fn, 'r')
            self.z = f['z'].value
            self.logM = f['logM'].value
            self.M = 10**self.logM
            #self.fcoll_tab = f['fcoll'].value
            self.dndm = f['dndm'].value

            if self.pf['hmf_load_ps']:
                self.k = f['k'].value
                self.bias_tab = f['bias'].value
                self.psCDM_tab = f['psCDM'].value
                self.sigma_tab = f['sigma'].value

            # Axes these?
            self.ngtm = f['ngtm'].value
            self.mgtm = f['mgtm'].value
            self.growth_tab = f['growth'].value
            
            f.close()
        elif re.search('.npz', self.fn):
            f = np.load(self.fn)
            self.z = f['z']
            self.logM = f['logM']
            self.M = 10**self.logM
            #self.fcoll_tab = f['fcoll']
            self.dndm = f['dndm']
            self.ngtm = f['ngtm']
            self.mgtm = f['mgtm']
            self.growth_tab = f['growth']
            self.sigma_tab = f['sigma']
            self.bias_tab = f['bias']
            self.psCDM_tab = f['psCDM']
            self.k = f['k']
            f.close()                        
        elif re.search('.pkl', self.fn):
            f = open(self.fn, 'rb')
            self.z = pickle.load(f)
            self.logM = pickle.load(f)
            self.M = 10**self.logM
            self.fcoll_spline_2d = pickle.load(f)
            self.dndm = pickle.load(f)            
            self.ngtm = pickle.load(f)
            self.mgtm = pickle.load(f)
            
            if self.pf['hmf_load_ps']:
                self.bias_tab = pickle.load(f)
                self.psCDM_tab = pickle.load(f)
                self.sigma_tab = pickle.load(f)
                self.k = pickle.load(f)
            
            if self.pf['hmf_load_growth']:
                self.growth_tab = pickle.load(f)
            
            # Axes these?
            self.ngtm = pickle.load(f)
            self.mgtm = pickle.load(f)

            f.close()

        else:
            raise IOError('Unrecognized format for hmf_table.')    
                
        self.lnM = np.log(self.M)
        self.dndlnm = self.M * self.dndm        
        self.Nz = self.z.size
        self.Nm = self.M.size
        
        self._is_loaded = True
        
    @property
    def growth_pars(self):
        if not hasattr(self, '_growth_pars'):
            self._growth_pars = {'dlna': self.pf['hmf_dlna']}
        return self._growth_pars
        
    @property
    def transfer_pars(self):
        if not hasattr(self, '_transfer_pars'):
            self._transfer_pars = \
                {'transfer__k_per_logint': self.pf['hmf_transfer__k_per_logint'],
                'transfer__kmax': self.pf['hmf_transfer__kmax']}
        return self._transfer_pars

    @property
    def MF(self):
        if not hasattr(self, '_MF'):

            self.logMmin_tab = self.pf['hmf_logMmin']
            self.logMmax_tab = self.pf['hmf_logMmax']
            self.dz = self.pf['hmf_dz']
            
            # Introduce ghost zones so that the derivative is defined
            # at the boundaries.
            self.zmin = max(self.pf['hmf_zmin'] - 2 * self.dz, 0)
            self.zmax = self.pf['hmf_zmax'] + 2 * self.dz
            self.dlogM = self.pf['hmf_dlogM']
            
            self.Nz = int(round(((self.zmax - self.zmin) / self.dz) + 1, 1))
            self.z = np.linspace(self.zmin, self.zmax, self.Nz)             

            # Initialize Perturbations class

            self._MF = MassFunction(Mmin=self.logMmin_tab, Mmax=self.logMmax_tab, 
                dlog10m=self.dlogM, z=self.z[0], 
                hmf_model=self.hmf_func, cosmo_params=self.cosmo_params,
                growth_params=self.growth_pars, sigma_8=self.cosm.sigma8, 
                n=self.cosm.primordial_index, transfer_params=self.transfer_pars,
                dlnk=self.pf['hmf_dlnk'], lnk_min=self.pf['hmf_lnk_min'],
                lnk_max=self.pf['hmf_lnk_max'])
                
        return self._MF

    @MF.setter
    def MF(self, value):
        self._MF = value     

    #@property
    #def fcoll_tab(self):
    #    if not hasattr(self, '_fcoll_tab'):
    #        self.build_fcoll_tab()
    #    return self._fcoll_tab    
    #
    #@fcoll_tab.setter
    #def fcoll_tab(self, value):
    #    self._fcoll_tab = value

    @property
    def cosmo_params(self):
        return {'Om0':self.cosm.omega_m_0,
                'Ob0':self.cosm.omega_b_0,
                'H0':self.cosm.h70*100}
                                
    def tabulate_hmf(self):
        """
        Build a lookup table for the halo mass function / collapsed fraction.
        
        Can be run in parallel.
        """    
        
        self.logMmin_tab = self.pf['hmf_logMmin']
        self.logMmax_tab = self.pf['hmf_logMmax']
        self.zmin = max(self.pf['hmf_zmin'] - 2*self.pf['hmf_dz'], 0.0)
        self.zmax = self.pf['hmf_zmax'] + 2*self.pf['hmf_dz']
        self.dlogM = self.pf['hmf_dlogM']
        self.dz = self.pf['hmf_dz']
        
        self.Nz = int(round(((self.zmax - self.zmin) / self.dz) + 1, 1))
        self.z = np.linspace(self.zmin, self.zmax, self.Nz)
        
        self.Nm = np.logspace(self.logMmin_tab, self.logMmax_tab, self.dlogM).size

        if rank == 0:
            print_hmf(self)
            print "\nComputing %s mass function..." % self.hmf_func    

        # Masses in hmf are in units of Msun * h
        self.M = self.MF.M / self.cosm.h70
        self.logM = np.log10(self.M)
        self.lnM = np.log(self.M)

        self.Nm = self.M.size

        self.k = self.MF.k * self.cosm.h70

        self.dndm = np.zeros([self.Nz, self.Nm])
        self.mgtm = np.zeros_like(self.dndm)
        self.ngtm = np.zeros_like(self.dndm)
        #fcoll_tab = np.zeros_like(self.dndm)
        
        # Extras
        self.bias_tab = np.zeros_like(self.dndm)
        self.psCDM_tab = np.zeros([len(self.z), len(self.k)])
        self.growth_tab = np.zeros_like(self.z)
        self.sigma_tab = np.zeros_like(self.M)
        
        pb = ProgressBar(len(self.z), 'fcoll')
        pb.start()

        for i, z in enumerate(self.z):
            
            if i > 0:
                self.MF.update(z=z)
                
            if i % size != rank:
                continue
                
            # Compute collapsed fraction
            #if self.hmf_func == 'PS' and self.hmf_analytic:
            #    delta_c = self.MF.delta_c / self.MF.growth_factor
            #    #fcoll_tab[i] = erfc(delta_c / sqrt2 / self.MF._sigma_0)
            #    
            #else:
                
            # Has units of h**4 / cMpc**3 / Msun
            self.dndm[i] = self.MF.dndm.copy() * self.cosm.h70**4
            self.mgtm[i] = self.MF.rho_gtm.copy() * self.cosm.h70**2
            self.ngtm[i] = self.MF.ngtm.copy() * self.cosm.h70**3
             
            # Remember that mgtm and mean_density have factors of h**2
            # so we're OK here dimensionally
            #fcoll_tab[i] = self.mgtm[i] / self.cosm.mean_density0
            
            # Eq. 3.53 and 3.54 in Steve's book
            #delta_b = 1. #?
            #delta_b0 = delta_b / self.growth_factor
            #nu_c = (self.delta_c - delta_b) / self.sigma
            #delta_c = self.delta_c - delta_b0
            
            delta_sc = (1. + z) * (3. / 5.) * (3. * np.pi / 2.)**(2./3.)
            # Not positive that this shouldn't just be sigma
            nu = (delta_sc / self.MF._sigma_0)**2
            
            # Cooray & Sheth (2002) Equations 68-69
            if self.hmf_func == 'PS':
                self.bias_tab[i] = 1. + (nu - 1.) / delta_sc
                    
            elif self.hmf_func == 'ST':
                ap, qp = 0.707, 0.3
                
                self.bias_tab[i] = 1. \
                    + (ap * nu - 1.) / delta_sc \
                    + (2. * qp / delta_sc) / (1. + (ap * nu)**qp)
            else:
                raise NotImplemented('No bias for non-PS non-ST MF yet!')
            
            self.psCDM_tab[i] = self.MF.power / self.cosm.h70**3
                        
            self.growth_tab[i] = self.MF.growth_factor            
                                    
            pb.update(i)
            
        pb.finish()
        
        # All processors will have this.
        self.sigma_tab = self.MF._sigma_0
                
        # Collect results!
        if size > 1:
            #tmp1 = np.zeros_like(fcoll_tab)
            #nothing = MPI.COMM_WORLD.Allreduce(fcoll_tab, tmp1)
            #_fcoll_tab = tmp1
            
            tmp2 = np.zeros_like(self.dndm)
            nothing = MPI.COMM_WORLD.Allreduce(self.dndm, tmp2)
            self.dndm = tmp2
            
            tmp3 = np.zeros_like(self.ngtm)
            nothing = MPI.COMM_WORLD.Allreduce(self.ngtm, tmp3)
            self.ngtm = tmp3
            
            tmp4 = np.zeros_like(self.mgtm)
            nothing = MPI.COMM_WORLD.Allreduce(self.mgtm, tmp4)
            self.mgtm = tmp4
            
            tmp5 = np.zeros_like(self.bias_tab)
            nothing = MPI.COMM_WORLD.Allreduce(self.bias_tab, tmp5)
            self.bias_tab = tmp5
            
            tmp6 = np.zeros_like(self.psCDM_tab)
            nothing = MPI.COMM_WORLD.Allreduce(self.psCDM_tab, tmp6)
            self.psCDM_tab = tmp6
            
            tmp7 = np.zeros_like(self.growth_tab)
            nothing = MPI.COMM_WORLD.Allreduce(self.growth_tab, tmp7)
            self.growth_tab = tmp7
            
        #else:
        #    _fcoll_tab = fcoll_tab   
                    
        # Fix NaN elements
        #_fcoll_tab[np.isnan(_fcoll_tab)] = 0.0
        #self._fcoll_tab = _fcoll_tab
        #
    @property
    def logM_min(self):
        if not hasattr(self, '_logM_min'):
            self.build_1d_splines(Tmin=self.pf['pop_Tmin'], mu=self.pf['mu'])
        return self._logM_min
        
    @logM_min.setter
    def logM_min(self, value):
        self._logM_min = value
    
    @property
    def fcoll_Tmin(self):
        if not hasattr(self, '_fcoll_Tmin'):
            self.build_1d_splines(Tmin=self.pf['pop_Tmin'], mu=self.pf['mu'])
        return self._fcoll_Tmin
        
    @fcoll_Tmin.setter
    def fcoll_Tmin(self, value):
        self._fcoll_Tmin = value    
    
    def build_1d_splines(self, Tmin, mu=0.6, return_fcoll=False, 
        return_fcoll_p=True, return_fcoll_pp=False):
        """
        Construct splines for fcoll and its derivatives given a (fixed) 
        minimum virial temperature.
        """
        
        Mmin_of_z = (self.pf['pop_Mmin'] is None) or \
            type(self.pf['pop_Mmin']) is FunctionType
        Mmax_of_z = (self.pf['pop_Tmax'] is not None) or \
            type(self.pf['pop_Mmax']) is FunctionType    
        
        self.logM_min = np.zeros_like(self.z)
        self.logM_max = np.inf * np.ones_like(self.z)
        self.fcoll_Tmin = np.zeros_like(self.z)
        self.dndm_Mmin = np.zeros_like(self.z)
        self.dndm_Mmax = np.zeros_like(self.z)
        for i, z in enumerate(self.z):
            if self.pf['pop_Mmin'] is None:
                self.logM_min[i] = np.log10(self.VirialMass(Tmin, z, mu=mu))
            else:
                if type(self.pf['pop_Mmin']) is FunctionType:
                    self.logM_min[i] = np.log10(self.pf['pop_Mmin'](z))
                else:    
                    self.logM_min[i] = np.log10(self.pf['pop_Mmin'])
                    
            if Mmax_of_z:
                self.logM_max[i] = np.log10(self.VirialMass(self.pf['pop_Tmax'], z, mu=mu))        
                self.dndm_Mmax[i] = 10**np.interp(self.logM_min[i], self.logM, 
                    np.log10(self.dndm[i,:]))
                    
            # For boundary term
            if Mmin_of_z:
                self.dndm_Mmin[i] = 10**np.interp(self.logM_min[i], self.logM, 
                    np.log10(self.dndm[i,:]))

            self.fcoll_Tmin[i] = self.fcoll_2d(z, self.logM_min[i])

        # Main term: rate of change in collapsed fraction in halos that were
        # already above the threshold.
        self.ztab, self.dfcolldz_tab = \
            central_difference(self.z, self.fcoll_Tmin)

        # Compute boundary term(s)
        if Mmin_of_z:
            self.ztab, dMmindz = \
                central_difference(self.z, 10**self.logM_min)

            bc_min = 10**self.logM_min[1:-1] * self.dndm_Mmin[1:-1] \
                * dMmindz / self.cosm.mean_density0

            self.dfcolldz_tab -= bc_min    

        if Mmax_of_z:
            self.ztab, dMmaxdz = \
                central_difference(self.z, 10**self.logM_max)
        
            bc_max = 10**self.logM_min[1:-1] * self.dndm_Mmax[1:-1] \
                * dMmaxdz / self.cosm.mean_density0
                
            self.dfcolldz_tab += bc_max

        # Maybe smooth things
        if self.pf['hmf_dfcolldz_smooth']:
            if int(self.pf['hmf_dfcolldz_smooth']) > 1:
                kern = self.pf['hmf_dfcolldz_smooth']
            else:
                kern = 3

            self.dfcolldz_tab = smooth(self.dfcolldz_tab, kern)
        
            if self.pf['hmf_dfcolldz_trunc']:
                self.dfcolldz_tab[0:kern] = np.zeros(kern)
                self.dfcolldz_tab[-kern:] = np.zeros(kern)
        
            # Cut off edges of array?
        
        # 'cuz time and redshift are different        
        self.dfcolldz_tab *= -1.

        if return_fcoll:
            fcoll_spline = interp1d(self.z, self.fcoll_Tmin, 
                kind=self.pf['hmf_interp'], bounds_error=False,
                fill_value=0.0)
        else:
            fcoll_spline = None
            
        self.dfcolldz_tab[self.dfcolldz_tab <= tiny_dfcolldz] = tiny_dfcolldz
                    
        spline = interp1d(self.ztab, np.log10(self.dfcolldz_tab), 
            kind='cubic', bounds_error=False, fill_value=np.log10(tiny_dfcolldz))
        dfcolldz_spline = lambda z: 10**spline.__call__(z)
        
        return fcoll_spline, dfcolldz_spline, None

    @property
    def tab_fcoll_2d(self):
        if not hasattr(self, '_tab_fcoll_2d'):
            self._tab_fcoll_2d = self.mgtm / self.cosm.mean_density0
            
            # May be unnecessary these days
            self._tab_fcoll_2d[np.isnan(self._tab_fcoll_2d)] = 0.0
            
        return self._tab_fcoll_2d
        
    @property
    def fcoll_spline_2d(self):
        if not hasattr(self, '_fcoll_spline_2d'):
            self._fcoll_spline_2d = RectBivariateSpline(self.z, 
                self.logM, self.tab_fcoll_2d, kx=3, ky=3)
        return self._fcoll_spline_2d
        
    @fcoll_spline_2d.setter
    def fcoll_spline_2d(self, value):
        self._fcoll_spline_2d = value

    #@property
    #def bias(self):
    #    if not hasattr(self, '_bias'):
    #        self._bias = RectBivariateSpline(self.z, 
    #            self.logM, self.bias_tab, kx=3, ky=3)
    #    return self._bias 
    #
    #@bias.setter
    #def bias(self, value):
    #    self._bias = value
        
    def bias_of_M(self, z):
        
        iz = np.argmin(np.abs(self.z - z))
               
        # Note also that this is also HMF's definition of nu
        delta_sc = 1.686
        nu = (delta_sc / self.sigma_0 / self.growth_factor[iz])**2  
        
        # Cooray & Sheth (2002) Equations 68-69
        if self.hmf_func == 'PS':
            bias = 1. + (nu - 1.) / delta_sc
        elif self.hmf_func == 'ST':
            ap, qp = 0.707, 0.3
            
            bias = 1. \
                + (ap * nu - 1.) / delta_sc \
                + (2. * qp / delta_sc) / (1. + (ap * nu)**qp)
        else:
            raise NotImplemented('No bias for non-PS non-ST MF yet!')
    
        return bias
    
    @property
    def psCDM(self):
        if not hasattr(self, '_psCDM'):
            self._psCDM = RectBivariateSpline(self.z, 
                self.k, self.psCDM_tab, kx=3, ky=3)
        return self._psCDM
        
    @psCDM.setter
    def psCDM(self, value):
        self._psCDM = value    
        
    @property
    def dlns_dlnm(self):
        if not hasattr(self, '_dlns_dlnm'):
            self.MF.update(z=0)
            self._dlns_dlnm = self.MF._dlnsdlnm
        return self._dlns_dlnm
    
    @property
    def sigma_0(self):
        return self.sigma_tab
                            
    @property
    def growth_factor(self):
        return self.growth_tab
    
    def fcoll_2d(self, z, logMmin):
        """
        Return fraction of mass in halos more massive than 10**logMmin.
        Interpolation in 2D, x = redshift = z, y = logMass.
        """ 
        
        if self.Mmax_ceil is not None:
            return np.squeeze(self.fcoll_spline_2d(z, logMmin)) \
                 - np.squeeze(self.fcoll_spline_2d(z, self.logMmax_ceil))
        elif self.pf['pop_Tmax'] is not None:
            logMmax = np.log10(self.VirialMass(self.pf['pop_Tmax'], z, 
                mu=self.pf['mu']))
                
            if logMmin >= logMmax:
                return tiny_fcoll

            return np.squeeze(self.fcoll_spline_2d(z, logMmin)) \
                 - np.squeeze(self.fcoll_spline_2d(z, logMmax))
        else:
            return np.squeeze(self.fcoll_spline_2d(z, logMmin))

    def dfcolldz(self, z):
        """
        Return derivative of fcoll(z).
        """
        
        return np.squeeze(self.dfcolldz_spline(z))
        
    def MAR_via_AM(self, z):
        """
        Compute mass accretion rate by abundance matching across redshift.
    
        Parameters
        ----------
        z : int, float
            Redshift.
    
        Returns
        -------
        Array of mass accretion rates, each element corresponding to the halo
        masses in self.M.
    
        """
            
        k = np.argmin(np.abs(z - self.z))
    
        if z not in self.z:
            print "WARNING: Rounding to nearest redshift z=%.3g" % self.z[k]
    
        # For some reason flipping the order is necessary for non-bogus results
        dn_gtm_1t = cumtrapz(self.dndlnm[k][-1::-1], 
            x=self.lnM[-1::-1], initial=0.)[-1::-1]
        dn_gtm_2t = cumtrapz(self.dndlnm[k-1][-1::-1], 
            x=self.lnM[-1::-1], initial=0.)[-1::-1]
    
        dn_gtm_1 = dn_gtm_1t[-1] - dn_gtm_1t
        dn_gtm_2 = dn_gtm_2t[-1] - dn_gtm_2t
    
        # Need to reverse arrays so that interpolants are in ascending order
        M_2 = np.exp(np.interp(dn_gtm_1[-1::-1], dn_gtm_2[-1::-1], 
            self.lnM[-1::-1])[-1::-1])
    
        # Compute time difference between z bins
        dz = self.z[k] - self.z[k-1]
        dt = dz * abs(self.cosm.dtdz(z)) / s_per_yr
    
        return np.maximum((M_2 - self.M) / dt, 0.0)
        
    @property
    def MAR_func(self):
        if not hasattr(self, '_MAR_func'):
            
            func = lambda zz: self.MAR_via_AM(zz)
            
            _MAR_tab = np.ones_like(self.dndm)
            for i, z in enumerate(self.z):
                _MAR_tab[i] = func(z)
            
            mask = np.zeros_like(_MAR_tab)
            mask[np.isnan(_MAR_tab)] = 1
            _MAR_tab[mask == 1] = 0.
            
            self._MAR_tab = np.ma.array(_MAR_tab, mask=mask)
            self._MAR_mask = mask    
            
            spl = RectBivariateSpline(self.z, self.lnM,
                self._MAR_tab, kx=3, ky=3)
                        
            self._MAR_func = lambda z, M: spl(z, np.log(M)).squeeze()
        
        return self._MAR_func
                                                   
    def VirialTemperature(self, M, z, mu=0.6):
        """
        Compute virial temperature corresponding to halo of given mass and
        collapse redshift.
        
        Equation 26 in Barkana & Loeb (2001).
        
        Parameters
        ----------
        M : float
            
        """    
        
        return 1.98e4 * (mu / 0.6) * (M / self.cosm.h70 / 1e8)**(2. / 3.) * \
            (self.cosm.omega_m_0 * self.cosm.CriticalDensityForCollapse(z) /
            self.cosm.OmegaMatter(z) / 18. / np.pi**2)**(1. / 3.) * \
            ((1. + z) / 10.)
        
    def VirialMass(self, T, z, mu=0.6):
        """
        Compute virial mass corresponding to halo of given virial temperature 
        and collapse redshift.
        
        Equation 26 in Barkana & Loeb (2001), rearranged.    
        """         
        
        return (1e8 / self.cosm.h70) * (T / 1.98e4)**1.5 * (mu / 0.6)**-1.5 \
            * (self.cosm.omega_m_0 * self.cosm.CriticalDensityForCollapse(z) \
            / self.cosm.OmegaMatter(z) / 18. / np.pi**2)**-0.5 \
            * ((1. + z) / 10.)**-1.5
                
    def VirialRadius(self, M, z, mu=0.6):
        """
        Compute virial radius corresponding to halo of given virial mass 
        and collapse redshift.
        
        Equation 24 in Barkana & Loeb (2001).
        """
        
        return 0.784 * (M / self.cosm.h70 / 1e8)**(1. / 3.) \
            * (self.cosm.omega_m_0 * self.cosm.CriticalDensityForCollapse(z) \
            / self.cosm.OmegaMatter(z) / 18. / np.pi**2)**(-1. / 3.) \
            * ((1. + z) / 10.)**-1.
              
    def MassFromVc(self, Vc, z):
        cterm = (self.cosm.omega_m_0 * self.cosm.CriticalDensityForCollapse(z) \
            / self.cosm.OmegaMatter(z) / 18. / np.pi**2)
        return (1e8 / self.cosm.h70) \
            *  (Vc / 23.4)**3 / cterm**0.5 / ((1. + z) / 10)**1.5
            
    def BindingEnergy(self, M, z, mu=0.6):
        return (0.5 * G * (M * g_per_msun)**2 / self.VirialRadius(M, z, mu)) \
            * self.cosm.fbaryon / cm_per_kpc
            
    def MassFromEb(self, z, Eb, mu=0.6):
        # Could do this analytically but I'm lazy
        func = lambda M: abs(np.log10(self.BindingEnergy(10**M, z=z, mu=mu)) - np.log10(Eb))
        return 10**fsolve(func, x0=7.)[0]
            
    def MeanDensity(self, M, z, mu=0.6):
        V = 4. * np.pi * self.VirialRadius(M, z, mu)**3 / 3.
        return (M / V) * g_per_msun / cm_per_kpc**3

    def JeansMass(self, M, z, mu=0.6):
        rho = self.MeanDensity(M, z, mu)
        T = self.VirialTemperature(M, z, mu)
        cs = np.sqrt(k_B * T / m_H)
        
        l = np.sqrt(np.pi * cs**2 / G / rho)
        return 4. * np.pi * rho * (0.5 * l)**3 / 3. / g_per_msun
        
    def _tegmark(self, z):
        fH2s = lambda T: 3.5e-4 * (T / 1e3)**1.52
        fH2c = lambda T: 1.6e-4 * ((1. + z) / 20.)**-1.5 \
            * (1. + (10. * (T / 1e3)**3.5) / (60. + (T / 1e3)**4))**-1. \
            * np.exp(512. / T)
    
        to_min = lambda T: abs(fH2s(T) - fH2c(T)) 
        Tcrit = fsolve(to_min, 2e3)[0]

        M = self.VirialMass(Tcrit, z)

        return M
    
    def Mmin_floor(self, zarr):
        if self.pf['feedback_streaming']:
            vbc = self.pf['feedback_vel_at_rec'] * (1. + zarr) / 1100.
            # Anastasia's "optimal fit"
            Vcool = np.sqrt(3.714**2 + (4.015 * vbc)**2)
            Mmin_vbc = self.MassFromVc(Vcool, zarr)
        else:
            Mmin_vbc = np.zeros_like(zarr)
        
        Mmin_H2 = np.array(map(self._tegmark, zarr))
                
        #return np.maximum(Mmin_vbc, Mmin_H2)      
        return Mmin_vbc + Mmin_H2
      
    def table_prefix(self, with_size=False):
        """
        What should we name this table?
        
        Convention:
        hmf_FIT_logM_nM_logMmin_logMmax_z_nz_
        
        Read:
        halo mass function using FIT form of the mass function
        using nM mass points between logMmin and logMmax
        using nz redshift points between zmin and zmax
        
        """
        
        M1, M2 = self.pf['hmf_logMmin'], self.pf['hmf_logMmax']
        z1, z2 = self.pf['hmf_zmin'], self.pf['hmf_zmax']
        
        if with_size:
            logMsize = (self.pf['hmf_logMmax'] - self.pf['hmf_logMmin']) \
                / self.pf['hmf_dlogM']                
            zsize = ((self.pf['hmf_zmax'] - self.pf['hmf_zmin']) \
                / self.pf['hmf_dz']) + 1
                
            assert logMsize % 1 == 0
            logMsize = int(logMsize)    
            assert zsize % 1 == 0
            zsize = int(round(zsize, 1))    
                
            return 'hmf_%s_logM_%s_%i-%i_z_%s_%i-%i' \
                % (self.hmf_func, logMsize, M1, M2, zsize, z1, z2)
        else:
            return 'hmf_%s_logM_*_%i-%i_z_*_%i-%i' \
                % (self.hmf_func, M1, M2, z1, z2) 
                               
    def save_hmf(self, fn=None, clobber=True, destination=None, format='hdf5'):
        """
        Save mass function table to HDF5 or binary (via pickle).
        
        Parameters
        ----------
        fn : str (optional)
            Name of file to save results to. If None, will use 
            self.table_prefix and value of format parameter to make one up.
        clobber : bool 
            Overwrite pre-existing files of the same name?
        destination : str
            Path to directory (other than CWD) to save table.
        format : str
            Format of output. Can be 'hdf5' or 'pkl'
        
        """

        try:
            import hmf
            hmf_v = hmf.__version__
        except AttributeError:
            hmf_v = 'unknown'
        
        # Do this first! (Otherwise parallel runs will be garbage)
        self.tabulate_hmf()    
        
        if rank > 0:
            return
        
        if destination is None:
            destination = '.'
        
        # Determine filename
        if fn is None:
            fn = '%s/%s.%s' % (destination, self.table_prefix(True), format)                
        else:
            if format not in fn:
                print "Suffix of provided filename does not match chosen format."
                print "Will go with format indicated by filename suffix."
        
        if os.path.exists(fn):
            if clobber:
                os.system('rm -f %s' % fn)
            else:
                raise IOError('File %s exists! Set clobber=True or remove manually.' % fn) 
            
        if format == 'hdf5':
            f = h5py.File(fn, 'w')
            f.create_dataset('z', data=self.z)
            f.create_dataset('logM', data=self.logM)
            #f.create_dataset('fcoll', data=self.tab_fcoll_2d)
            f.create_dataset('dndm', data=self.dndm)
            f.create_dataset('ngtm', data=self.ngtm)
            f.create_dataset('mgtm', data=self.mgtm)            
            f.create_dataset('bias', data=self.bias_tab)            
            f.create_dataset('psCDM', data=self.psCDM_tab)
            f.create_dataset('growth', data=self.growth_tab)
            f.create_dataset('sigma', data=self.sigma_tab)
            f.create_dataset('k', data=self.k)
            f.create_dataset('hmf-version', data=hmf_v)         
            f.close()

        elif format == 'npz':
            data = {'z': self.z, 'logM': self.logM, 
                    'dndm': self.dndm,
                    'ngtm': self.ngtm, 'mgtm': self.mgtm,
                    'pars': {'growth_pars': self.growth_pars,
                             'transfer_pars': self.transfer_pars},
                    'growth': self.growth_tab,
                    'bias': self.bias_tab,
                    'psCDM': self.psCDM_tab,
                    'sigma': self.sigma_tab,
                    'k': self.k,
                    'hmf-version': hmf_v}
            np.savez(fn, **data)

        # Otherwise, pickle it!    
        else:   
            f = open(fn, 'wb')
            pickle.dump(self.z, f)
            pickle.dump(self.logM, f)
            pickle.dump(self.fcoll_spline_2d, f)
            pickle.dump(self.dndm, f)
            pickle.dump(self.ngtm, f)
            pickle.dump(self.mgtm, f)
            pickle.dump(self.bias_tab, f)
            pickle.dump(self.psCDM_tab, f)
            pickle.dump(self.sigma_tab, f)
            pickle.dump(self.k)
            pickle.dump(self.growth_tab, f)
            pickle.dump({'growth_pars': self.growth_pars,
                'transfer_pars': self.transfer_pars}, f)
            pickle.dump(dict(('hmf-version', hmf_v)))
            f.close()
            
        print 'Wrote %s.' % fn
        return
        
