:orphan:

Fitting Galaxy Luminosity Functions
-----------------------------------
If you've already had a look at :doc:`example_mcmc_gs`, the approach detailed below will look familiar: we'll define a dataset to be fit, a set of parameters that each model requires, and a set of parameters allowed to vary in the fit (and priors that tell us how much they are allowed to vary).

One notable distinction between this example and the last is that the "blobs" we are interested in are two dimensional (e.g., we're generally interested in the galaxy luminosity function as a function of both magnitude and redshift), and so we must pay some attention to setting up the calculation. In this example, we'll keep track of the luminosity function and the star formation efficiency. The latter is the thing our parameters describe, so so while we could re-generate the SFE later (for each element of the chain) at very little extra computational cost, we may as well save it. We could also simply re-generate the LF later, but that is a slightly less trivial cost, so it'll save some time to track it as well.

OK, each of these quantities has a different independent variable, but we may as well track them at a common set of redshifts:

::

    # Independent variables
    redshifts = np.array([3, 3.8, 4, 4.9, 5, 5.9, 6, 6.9, 7, 7.9, 8])
    MUV = np.arange(-28, -8.8, 0.2)
    Mh = np.logspace(7, 13, 61)

    # blob #1: the LF. Give it a name, and the function needed to calculate it.
    blob_n1 = ['galaxy_lf']
    blob_i1 = [redshifts, MUV]
    blob_f1 = ['pops[0].LuminosityFunction']
   
   # blob #2: SFE. 
    blob_n2 = ['fstar']
    blob_i2 = [redshifts, Mh]
    blob_f2 = ['pops[0].fstar']
    
    # Stick this all in a dictionary
    blob_pars = \
    { 
     'blob_names': [blob_n1, blob_n2],
     'blob_ivars': [blob_i1, blob_i2],
     'blob_funcs': [blob_f1, blob_f2],
    }
    
Note that the ``blob_f?`` variables contain string representations of functions. This is important! In :doc:`example_mcmc_gs`, we didn't have to do this because we only tracked common blobs that live in the ``history`` attribute of the ``ares.simulations.Global21cm`` class (*ares* knows to first look for blobs in the ``history`` attribute of simulation objects). So, dealing with 2-D blobs requires some knowledge of what's happening in the code. For example, the above will only work if ``LuminosityFunction`` accepts redshift and UV magnitude **in that order**. Also, we had to know that this method is attached to the object stored in the ``pops[0]`` attribute of a simulation object.

Now, let's make our master dictionary of parameters, with one important addition:
        
::

        
            
    base_pars = ares.util.ParameterBundle('mirocha2016:dpl')
    base_pars.update(blob_pars)
    
    # This is important!
    base_pars['pop_calib_L1600{0}'] = None
    
The ``pop_calib_L1600`` parameter tells *ares* the :math:`1600\AA` luminosity per unit star formation conversion used to derive the input SFE parameters. This can be useful, for example, if you'd like to vary the parameters of a stellar population (e.g., the metallicity ``pop_Z``) *without* impacting the luminosity function. Of course, when we're fitting the LF, the whole point to allow parameter variations to affect the LF, which is why we must turn it off by hand here.
    
.. note:: By default, *ares* does not apply a dust correction. This can be useful, for example, if you want to generate a single physical model and study the effects of dust after the fact (see :doc:`example_galaxypop`). However, when fitting data, we must make a choice about the dust correction ahead of time since each evaluation of the likelihood will depend on it. Let's take a simple one    
    
    
OK, now let's set the free parameters and priors:
    
::

    free_pars = \
      [
       'php_func_par0{0}[0]',
       'php_func_par1{0}[0]', 
       'php_func_par2{0}[0]',
       'php_func_par3{0}[0]',
      ]
    
    is_log = [True, True, False, False]
    
    ps = ares.inference.PriorSet()
    ps.add_prior(ares.inference.Priors.UniformPrior(-3, 0.), 'php_func_par0{0}[0]')
    ps.add_prior(ares.inference.Priors.UniformPrior(9, 13),  'php_func_par1{0}[0]')
    ps.add_prior(ares.inference.Priors.UniformPrior(0, 2),   'php_func_par2{0}[0]')
    ps.add_prior(ares.inference.Priors.UniformPrior(-2, 0),   'php_func_par3{0}[0]')
    
    
Some initial guesses (optional?):

::
    
    guesses = \
    {
     'php_func_par0{0}[0]': -1,
     'php_func_par1{0}[0]': 11.5,
     'php_func_par2{0}[0]': 0.5,
     'php_func_par3{0}[0]': -0.5,
    }
    
Initialize the fitter object, and go!

::
            
    # Initialize a fitter object and give it the data to be fit
    fitter = ares.inference.FitLuminosityFunction(**base_pars)
    
    fitter.parameters = free_pars
    fitter.is_log = is_log
    fitter.prior_set = ps
    
    # Setup # of walkers and initial guesses for them
    fitter.nwalkers = 192 
    
    # The data can also be provided more explicitly
    fitter.data = 'bouwens2015'
    
    fitter.jitter = [0.1] * len(free_pars)
    fitter.guesses = guesses 
    
    fitter.runsim = False
    fitter.save_hmf = True  # cache HMF for a speed-up!
    fitter.save_psm = True  # cache source SED model (e.g., BPASS, S99)
    
    # Setting this flag will make *ares* generate new files for each checkpoint. 
    # 2-D blobs can get large, so this allows us to just download a single
    # snapshot or two if we'd like (useful if running on remote machine)
    fitter.checkpoint_append = False
    
    # Run the thing
    fitter.run('test_lfcal', burn=100, steps=200, save_freq=20, clobber=True)


See :doc:`example_mcmc_analysis` for general instructions for dealing with the outputs of MCMC calculations.
