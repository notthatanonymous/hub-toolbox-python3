"""
Applies Mutual Proximity (MP) [1] on a distance matrix. The return value is
converted to a distance matrix again. The resulting distance matrix
should show lower hubness.

This file is part of the HUB TOOLBOX available at
http://ofai.at/research/impml/projects/hubology.html
(c) 2013, Dominik Schnitzer <dominik.schnitzer@ofai.at>

Usage:
  Dmp = mutual_proximity(D, type) - Applies MP on the distance matrix 'D'
     using the selected variant ('type'). The transformed distance matrix
     is returned.

Possible types:
  'empiric': Uses the Empirical distribution to perform Mutual Proximity.
  'gauss': (requires the Statistics Toolbox (the mvncdf() function)
     Assumes that the distances are Gaussian distributed.
  'gaussi': Assumes that the distances are independently Gaussian
     distributed. (fastest Variante)
  'gammai': Assumes that the distances follow a Gamma distribution and
     are independently distributed.

[1] Local and global scaling reduce hubs in space, 
Schnitzer, Flexer, Schedl, Widmer, Journal of Machine Learning Research 2012

This file was ported from MATLAB(R) code to Python3
by Roman Feldbauer <roman.feldbauer@ofai.at>

@author: Roman Feldbauer
@date: 2015-09-25
"""

import numpy as np
from scipy.special import gammainc
from scipy.stats import norm, mvn
from scipy.sparse import issparse, lil_matrix
from enum import Enum
import multiprocessing as mp
#from numba import jit
from hub_toolbox import IO, Logging

class Distribution(Enum):
    empiric = 'empiric'
    gauss = 'gauss'
    gaussi = 'gaussi'
    gammai = 'gammai'

def _get_weighted_batches(n, jobs):
    """Define batches with increasing size to average the runtime for each
    batch.
    
    Observation: MP gets faster while processing the distance matrix. This 
    approximately follows a linear function.
    Idea: Give each row a weight w according to w(r) = 1 - r/n, where 
    r... row number, n... number of rows. Now each batch should get the same
    weight bw := n/jobs, where jobs... number of processes. The equation to 
    solve is then sum[r=a..b]( 1-r/n ) = n / 2jobs. 
    Solving for b yields the formula used below. (The last batch may get
    additional left-over rows).    
    """
     
    batches = []    
    a = 0
    b = 0
    for i in range(jobs-1):  # @UnusedVariable
        b = int((np.sqrt(jobs)*(2*n-1)-np.sqrt(jobs*(-2*a+2*n+1)**2-4*n**2))/(2*np.sqrt(jobs)))
        if b < 1:
            b = 1 # Each batch must contain at least 1 row
        batches.append( np.arange(a, b) )
        a = b 
    if b < n-1:
        batches.append( np.arange(a, n) )
    return batches

def _worker(work_input, work_output):
    """A helper function for cv parallelization."""
    for func, args in iter(work_input.get, 'STOP'):
        result = func(*args)
        work_output.put(result)
        
#===============================================================================
# def _calculate(self, func, args):
#     """A helper function for cv parallelization."""
#     return func(*args)
#===============================================================================


class MutualProximity():
    """Transform a distance matrix with Mutual Proximity.
    
    """
    
    def __init__(self, D, isSimilarityMatrix=False, tmp='/tmp/'):
        self.D = IO.copy_D_or_load_memmap(D, writeable=True)
        self.log = Logging.ConsoleLogging()
        if isSimilarityMatrix:
            self.self_value = 1
        else:
            self.self_value = 0
        self.isSimilarityMatrix = isSimilarityMatrix
        self.tmp = tmp
        
    def calculate_mutual_proximity(self, distrType=None, test_set_mask=None, 
                                   verbose=False, enforce_disk=False,
                                   sample_size=0, filename=None, empspex=False,
                                   n_jobs=-1):
        """Apply MP on a distance matrix."""
        
        if test_set_mask is not None:
            train_set_mask = np.setdiff1d(np.arange(self.D.shape[0]), test_set_mask)
        else:
            train_set_mask = np.ones(self.D.shape[0], np.bool)
            
        if distrType is None:
            self.log.message("No Mutual Proximity type given. "
                             "Using: Distribution.empiric. "
                             "For fast results use: Distribution.gaussi")
            Dmp = self.mp_empiric(train_set_mask, verbose, empspex, n_jobs)
        else:
            if distrType == Distribution.empiric:
                Dmp = self.mp_empiric(train_set_mask, verbose, empspex, n_jobs)
            elif distrType == Distribution.gauss:
                Dmp = self.mp_gauss(train_set_mask, verbose, n_jobs)
            elif distrType == Distribution.gaussi:
                Dmp = self.mp_gaussi(train_set_mask, verbose, enforce_disk, 
                                     sample_size, filename, n_jobs)
            elif distrType == Distribution.gammai:
                Dmp = self.mp_gammai(train_set_mask, verbose, n_jobs)
            else:
                self.log.warning("Valid Mutual Proximity type missing!\n"+\
                             "Use: \n"+\
                             "mp = MutualProximity(D, Distribution.empiric|"+\
                             "Distribution.gauss|Distribution.gaussi|"+\
                             "Distribution.gammi)\n"+\
                             "Dmp = mp.calculate_mutual_proximity()")
                Dmp = np.array([])
       
        return Dmp
         
    def _partial_mp_empiric_sparse_exact(self, batch, matrix, idx, n, verbose):
        Dmp = np.zeros((len(batch), self.D.shape[1]), dtype=np.float32)
        for i, b in enumerate(batch):
            if verbose and ((batch[i]+1)%1000 == 0 or batch[i]==n-1 or i==len(batch)-1 or i==0):
                self.log.message("MP_empiric_sparse_exact: {} of {}. On {}."
                                 .format(batch[i]+1, n, mp.current_process().name), flush=True)  # @UndefinedVariable
            for j in range(b+1, n):
                d = matrix[j, b]
                dI = matrix[b, :].todense()
                dJ = matrix[j, :].todense()
                 
                if self.isSimilarityMatrix:
                    sIJ_intersect = ((dI <= d) & (dJ <= d)).sum()
                    sIJ_overlap = sIJ_intersect / n
                else:
                    sIJ_intersect = ((dI > d) & (dJ > d)).sum()
                    sIJ_overlap = 1 - (sIJ_intersect / n)
                Dmp[i, j] = sIJ_overlap
                #need to mirror later!!
        filenamewords = [str(batch[0]), str(batch[-1]), 'triu']
        f = self.tmp + '_'.join(filenamewords)
        self.log.message("MP_empiric_sparse_exact: Saving batch {}-{} to {}. On {}."
                        .format(batch[0], batch[-1], f, mp.current_process().name), flush=True)  # @UndefinedVariable
        np.save(f, Dmp)
        return (batch, Dmp)
    
    def _partial_mp_empiric_sparse(self, batch, matrix, idx, n, verbose):
        Dmp = lil_matrix((len(batch), self.D.shape[1]), dtype=np.float32)
        for i, b in enumerate(batch):
            if verbose and ((batch[i]+1)%1000 == 0 or batch[i]==n-1 or i==len(batch)-1 or i==0):
                self.log.message("MP_empiric_sparse: {} of {}. On {}."
                                 .format(batch[i]+1, n, mp.current_process().name), flush=True)  # @UndefinedVariable
            for j in range(b+1, n):
                d = matrix[j, b]
                if d>0: 
                    nnz = np.max([self.D[i].nnz, self.D[j].nnz])
                    dI = self.D[i, :].todense()
                    dJ = self.D[j, :].todense()
                    
                    if self.isSimilarityMatrix:
                        sIJ_intersect = ((dI <= d) & (dJ <= d)).sum()
                        sIJ_overlap = sIJ_intersect / nnz
                    else:
                        sIJ_intersect = ((dI > d) & (dJ > d)).sum()
                        sIJ_overlap = 1 - (sIJ_intersect / nnz)
                    Dmp[i, j] = sIJ_overlap
                    # need to mirror later
                else:
                    pass # skip zero entries
        
        filenamewords = [str(batch[0]), str(batch[-1]), 'triu']
        f = self.tmp + '_'.join(filenamewords)
        self.log.message("MP_empiric_sparse: Saving batch {}-{} to {}. On {}."
                        .format(batch[0], batch[-1], f, mp.current_process().name), flush=True)  # @UndefinedVariable
        np.save(f, Dmp)
        return (batch, Dmp)
    
    def mp_empiric_sparse(self, train_set_mask=None, verbose=False, empspex=False, n_jobs=-1):
        n = np.shape(self.D)[0]
        
        if empspex:
            Dmp = np.zeros(self.D.shape, dtype=np.float32)
        else:
            Dmp = lil_matrix(self.D.shape)
            
        # Parallelization
        if n_jobs == -1: # take all cpus
            NUMBER_OF_PROCESSES = mp.cpu_count()  # @UndefinedVariable
        else:
            NUMBER_OF_PROCESSES = n_jobs
        tasks = []
        
        batches = _get_weighted_batches(n, NUMBER_OF_PROCESSES)
        
        for idx, batch in enumerate(batches):
            matrix = self.D.copy()
            if empspex:
                tasks.append((self._partial_mp_empiric_sparse_exact, (batch, matrix, idx, n, verbose)))   
            else:
                tasks.append((self._partial_mp_empiric_sparse, (batch, matrix, idx, n, verbose)))
        
        task_queue = mp.Queue()     # @UndefinedVariable
        done_queue = mp.Queue()     # @UndefinedVariable
        
        for task in tasks:
            task_queue.put(task)
            
        processes = []
        for i in range(NUMBER_OF_PROCESSES):  # @UnusedVariable
            processes.append(mp.Process(target=_worker, args=(task_queue, done_queue))) # @UndefinedVariable
            if verbose:
                self.log.message("Starting {}".format(processes[i].name))
            processes[i].start()  
        
        for i in range(len(tasks)):  # @UnusedVariable
            rows, Dmp_part = done_queue.get()
            task_queue.put('STOP')
            if verbose:
                self.log.message("Merging submatrix {} (rows {}..{})".format(i, rows[0], rows[-1]), flush=True)
            Dmp[rows, :] = Dmp_part
            
        #===================================================================
        # for i in range(NUMBER_OF_PROCESSES):  # @UnusedVariable
        #     if verbose:
        #         self.log.message("Stopping MP process {}".format(i), flush=True)
        #     task_queue.put('STOP')
        #===================================================================
            
        for p in processes:
            if verbose:
                self.log.message("Joining {}".format(p.name))
            p.join()

        import os, datetime
        time = datetime.datetime.strftime(datetime.datetime.now(), '%Y-%m-%d-%H-%M-%S')
        folder = self.tmp + 'feldbauer/hubPDBs2/'
        if empspex:
            filename = 'D_mpempspex_triu_'+time
        else:
            filename = 'D_mpemp_triu_'+time
        os.makedirs(folder, exist_ok=True)
        if verbose:
            self.log.message("Saving Dmp upper to {} as {}.npy".format(folder, filename), flush=True)
        np.save(folder + filename, Dmp)
        
        if verbose:
            self.log.message("Mirroring distance matrix", flush=True)
        Dmp += Dmp.T
    
        if self.isSimilarityMatrix:
            if verbose:
                self.log.message("Setting self distances", flush=True)
            if empspex:
                np.fill_diagonal(Dmp, self.self_value) #need to set self values
            else:
                for i in range(n):
                    Dmp[i, i] = self.self_value
            
        if not empspex:
            Dmp = Dmp.tocsr()
            
        return Dmp
    
        #=======================================================================
        # else:
        #     #nnz = self.D.nnz
        #     
        #     for i in range(n-1):
        #         if verbose and ((i+1)%100 == 0 or i==0 or i==n):
        #             self.log.message("MP_empiric: {} of {}.".format(i+1, n-1), 
        #                              flush=True)
        #         for j in range(i+1, n):
        #             d = self.D[j, i]
        #             if d>0: 
        #                 nnz = np.max(self.D[i].nnz, self.D[j].nnz)
        #                 dI = self.D[i, :].todense()
        #                 dJ = self.D[j, :].todense()
        #                 
        #                 
        #                 if self.isSimilarityMatrix:
        #                     sIJ_intersect = ((dI <= d) & (dJ <= d)).sum()
        #                     sIJ_overlap = sIJ_intersect / nnz
        #                 else:
        #                     sIJ_intersect = ((dI > d) & (dJ > d)).sum()
        #                     sIJ_overlap = 1 - (sIJ_intersect / nnz)
        #                 Dmp[i, j] = sIJ_overlap
        #                 Dmp[j, i] = sIJ_overlap
        #             else:
        #                 pass # skip zero entries
        #     
        #     if self.isSimilarityMatrix:
        #         for i in range(n):
        #             Dmp[i, i] = self.self_value #need to set self values
        #     
        #     return Dmp.tocsr()
        #=======================================================================
    
    def mp_empiric(self, train_set_mask=None, verbose=False, empspex=False, n_jobs=-1):
        """Compute Mutual Proximity distances with empirical data (slow)."""   
        #TODO implement train_set_mask!
        if not np.all(train_set_mask):
            self.log.error("MP empiric does not support train/test splits yet.")
            return
        #=======================================================================
        # if self.isSimilarityMatrix:
        #     self.log.warning("Similarity-based empiric MP support is still experimental.")
        #=======================================================================
        if issparse(self.D):
            #===================================================================
            # self.log.warning("Sparse matrix support is still experimental.")
            #===================================================================
            return self.mp_empiric_sparse(train_set_mask, verbose, empspex, n_jobs)
            
        if isinstance(self.D, np.memmap): # work on disk
            from tempfile import mkstemp
            filename = mkstemp(suffix='pytmp')[1] # [0]... fd, [1]... filename
            self.log.message("Writing rescaled distance matrix to file:", filename)
            Dmp = np.memmap(filename, dtype='float64', mode='w+', shape=self.D.shape)
            n = self.D.shape[0]
            np.fill_diagonal(self.D, self.self_value)
            
            for i in range(n-1):
                if verbose and ((i+1)%1000 == 0 or i==n):
                    self.log.message("MP_empiric: {} of {}.".format(i+1, n-1), flush=True)
                j_idx = np.arange(i+1, n)
                j_len = np.size(j_idx, 0)
               
                dI = np.tile(self.D[i, :], (j_len, 1))
                dJ = self.D[j_idx, :]
                d = np.tile(self.D[j_idx, i][:, np.newaxis], (1, n))
                
                if self.isSimilarityMatrix:
                    sIJ_intersect = np.sum((dI <= d) & (dJ <= d), 1)
                    sIJ_overlap = sIJ_intersect / n
                    Dmp[i, i] = self.self_value
                else: 
                    sIJ_intersect = np.sum((dI > d) & (dJ > d), 1)
                    sIJ_overlap = 1 - (sIJ_intersect / n)
                Dmp[i, j_idx] = sIJ_overlap 
            Dmp += Dmp.T   
              
        else: # work in memory
            if not issparse(self.D):
                # ensure correct self distances (NOT done for sparse matrices!)
                np.fill_diagonal(self.D, self.self_value)
            n = np.shape(self.D)[0]
            Dmp_list = [0 for i in range(n)]
             
            for i in range(n-1):
                if verbose and ((i+1)%1000 == 0 or i==n):
                    self.log.message("MP_empiric: {} of {}.".format(i+1, n-1), flush=True)
                # Select only finite distances for MP
                j_idx = np.arange(i+1, n)
                j_len = np.size(j_idx, 0)
                 
                dI = np.tile(self.D[i, :], (j_len, 1))
                dJ = self.D[j_idx, :]
                d = np.tile(self.D[j_idx, i][:, np.newaxis], (1, n))
                 
                if self.isSimilarityMatrix:
                    sIJ_intersect = np.sum((dI <= d) & (dJ <= d), 1)
                    sIJ_overlap = sIJ_intersect / n
                else:
                    sIJ_intersect = np.sum((dI > d) & (dJ > d), 1)
                    sIJ_overlap = 1 - (sIJ_intersect / n)
                Dmp_list[i] = sIJ_overlap
                 
            Dmp = np.zeros(np.shape(self.D), dtype=self.D.dtype)
            for i in range(n-1):
                j_idx = np.arange(i+1, n)
                Dmp[i, j_idx] = Dmp_list[i]
                Dmp[j_idx, i] = Dmp_list[i]
                
            if self.isSimilarityMatrix:
                for i in range(n):
                    Dmp[i, i] = self.self_value
            
        return Dmp
    
    def mp_gauss(self, train_set_mask=None, verbose=False, n_jobs=-1):
        """Compute Mutual Proximity distances with Gaussian model (very slow)."""
        
        if n_jobs is not None:
            self.log.error("MP gauss does not support parallelization so far.")
        if self.isSimilarityMatrix:
            self.log.warning("Similarity-based Gaussian MP support is still experimental.")
        
        if not issparse(self.D):
            np.fill_diagonal(self.D, self.self_value)
        else:
            self.log.error("Sparse matrices not supported yet.")
            return
        
        mu = np.mean(self.D[train_set_mask], 0)
        sd = np.std(self.D[train_set_mask], 0, ddof=1)
                
        #Code for the BadMatrixSigma error
        eps = np.spacing(1)
        epsmat = np.array([[1e5 * eps, 0], [0, 1e5 * eps]])
                
        if isinstance(self.D, np.memmap):
            from tempfile import mkstemp
            filename = mkstemp(suffix='pytmp')[1] # [0]... fd, [1]... filename
            self.log.message("Writing rescaled distance matrix to file:", filename)
            Dmp = np.memmap(filename, dtype='float64', mode='w+', shape=self.D.shape)
        else:
            Dmp = np.zeros(np.shape(self.D), dtype=self.D.dtype)
        n = np.size(self.D, 0)
        
        for i in range(n):
            if verbose and ((i+1)%1000 == 0 or i+1==n):
                self.log.message("MP_gauss: {} of {}.".format(i+1, n))
            for j in range(i+1, n):
                c = np.cov(self.D[[i,j], :])
                x = np.array([self.D[i, j], self.D[j, i]])
                m = np.array([mu[i], mu[j]])
                
                low = np.tile(np.finfo(np.float32).min, 2)
                p12 = mvn.mvnun(low, x, m, c)[0] # [0]...p, [1]...inform
                if np.isnan(p12):
                    power = 7
                    while np.isnan(p12):
                        c += epsmat * (10**power) 
                        p12 = mvn.mvnun(low, x, m, c)[0]
                        power += 1
                    self.log.warning("p12 is NaN: i={}, j={}. "
                                     "Increased cov matrix by O({}).".format(
                                     i, j, epsmat[0,0]*(10**power)))
                
                if self.isSimilarityMatrix:
                    Dmp[j, i] = p12
                else:
                    p1 = norm.cdf(self.D[j, i], mu[i], sd[i])
                    p2 = norm.cdf(self.D[j, i], mu[j], sd[j])
                    Dmp[j, i] = p1 + p2 - p12
                
                Dmp[i, j] = Dmp[j, i]
            if self.isSimilarityMatrix:
                Dmp[i, i] = self.self_value

        return Dmp
    
    
    def mp_gaussi_sparse(self, train_set_mask, verbose):
        n = self.D.shape[0]
        from sklearn.utils.sparsefuncs_fast import csr_mean_variance_axis0  # @UnresolvedImport
        mu, var = csr_mean_variance_axis0(self.D[train_set_mask])
        sd = np.sqrt(var)
        del var
        
        Dmp = lil_matrix(self.D.shape)

        for i in range(n):
            if verbose and ((i+1)%1000 == 0 or i+1==n):
                self.log.message("MP_gaussi: {} of {}."
                                 .format(i+1, n), flush=True)
            j_idx = np.arange(i+1, n)
            #j_len = np.size(j_idx)
            
            Dij = self.D[i, j_idx].toarray().ravel() #Extract dense rows temporarily
            Dji = self.D[j_idx, i].toarray().ravel() #for vectorization below.
            
            p1 = norm.cdf(Dij, mu[i], sd[i]) # mu, sd broadcasted
            p1[Dij==0] = 0
            del Dij
            p2 = norm.cdf(Dji, 
                          mu[j_idx], 
                          sd[j_idx])
            p2[Dji==0] = 0
            del Dji
            #del mu, sd # with del mu, sd, error in line with mu broadcasting
            tmp = (p1 * p2).ravel()
            Dmp[i, i] = self.self_value
            Dmp[i, j_idx] = tmp            
            Dmp[j_idx, i] = tmp[:, np.newaxis]   
            del tmp, j_idx
    
        # non-vectorized code
        #=======================================================================
        # for i in range(n):
        #     if verbose and ((i+1)%1000 == 0 or i+1==n):
        #         self.log.message("MP_gaussi: {} of {}."
        #                          .format(i+1, n), flush=True)
        #     for j in range(i+1, n):
        #         if self.D[i, j] > 0:       
        #             if self.isSimilarityMatrix:
        #                 p1 = norm.cdf(self.D[i, j], mu[i], sd[i])
        #                 p2 = norm.cdf(self.D[j, i], mu[j], sd[j])
        #                 Dmp[i, j] = (p1 * p2).ravel()
        #             else:
        #                 p1 = 1 - norm.cdf(self.D[i, j], mu[i], sd[i])
        #                 p2 = 1 - norm.cdf(self.D[j, i], mu[j], sd[j])
        #                 Dmp[i, j] = (1 - p1 * p2).ravel()
        #             Dmp[j, i] = Dmp[i, j]
        #=======================================================================
        
        return Dmp.tocsr()

    def mp_gaussi(self, train_set_mask=None, verbose=False, enforce_disk=False,
                  sample_size=0, filename=None, n_jobs=-1):
        """Compute Mutual Proximity modeled with independent Gaussians (fast). 
        Use enforce_disk=True to use memory maps for matrices that do not fit 
        into main memory.
        Set sample_size=SIZE to estimate Gaussian distribution from SIZE 
        samples. Default sample_size=0: use all points."""
        
        if n_jobs is not None:
            self.log.error("MP gaussi does not support parallelization so far.")
            
        if self.isSimilarityMatrix:
            self.log.warning("Similarity-based I.Gaussian MP support is still experimental.")
        
        if verbose:
            self.log.message('Mutual proximity rescaling started.', flush=True)
        n = np.size(self.D, 0)
        if not isinstance(self.D, np.memmap) and not issparse(self.D):
            np.fill_diagonal(self.D, self.self_value)
        # else: do this later on the fly
        if issparse(self.D):
            #self.log.error("Sparse matrices not supported yet.")
            return self.mp_gaussi_sparse(train_set_mask, verbose)
        
        # Get memory info
        free_mem = IO.FreeMemLinux(unit='k').user_free
        req_mem = self.D.nbytes
        mem_ratio = int(req_mem / free_mem ) 
                
        # Calculate mean and std
        if mem_ratio < 2.0 and not enforce_disk:
            if verbose:
                self.log.message('Calculating distribution parameters in memory.'
                                'Number of samples for parameter estimation: {}'.
                                format(sample_size))
            if sample_size != 0:
                samples = np.random.shuffle(train_set_mask)[0:sample_size]
                mu = np.mean(self.D[samples], 0)
                sd = np.std(self.D[samples], 0, ddof=1)
            else:
                mu = np.mean(self.D[train_set_mask], 0)
                sd = np.std(self.D[train_set_mask], 0, ddof=1)
        # ... using tertiary memory, if necessary
        else:
            if verbose:
                self.log.message('Calculating Gaussian parameters on disk. '
                                'Number of samples for parameter estimation: '
                                '{}'.format(sample_size))
            if not np.all(train_set_mask) and isinstance(self.D, np.memmap):
                raise NotImplementedError("Using train/test splits and working "
                                          "and working on disk at the same time"
                                          "not supported atm.")
            mu = np.zeros(self.D.shape[1])
            sd = np.zeros_like(mu)
            
            if sample_size != 0:
                idx = np.arange(self.D.shape[0])
                np.random.shuffle(idx)
                samples = idx[0:sample_size]
                
                for i, row in enumerate(self.D[samples].T):
                    if verbose and ((i+1)%10000 == 0 or i+1 == n):
                        self.log.message("MP_gaussi mean/std: "
                                        "{} of {}.".format(i+1, n), flush=True)
                    mu[i] = row.mean()
                    sd[i] = row.std(ddof=1)    
            else:
                for i, row in enumerate(self.D.T):
                    if verbose and ((i+1)%10000 == 0 or i+1 == n):
                        self.log.message("MP_gaussi mean/std: "
                                        "{} of {}.".format(i+1, n), flush=True)
                    mu[i] = row.mean()
                    sd[i] = row.std(ddof=1)                            
        
        # work on disk
        if isinstance(self.D, np.memmap) or enforce_disk:
            if filename is None:
                # create distance matrix on disk
                from tempfile import mkstemp
                filename = mkstemp(suffix='pytmp')[1] # [0]... fd, [1]... filename
            #else: 
            #    filename was provided via function call
            self.log.message("Writing rescaled distance matrix to file:", filename)
            Dmp = np.memmap(filename, dtype='float64', mode='w+', shape=self.D.shape)
            # determine number of rows that fit into free memory
            #------------- max_rows = int(free_mem / n / 8) # 8 byte per float64
            # take_rows = int(max_rows / 8) # allow the current matrices to occupy 1/4 of the available memory
            #-------------------------- nr_batches = int(np.ceil(n / take_rows))
            nr_batches, take_rows = IO.matrix_split(n, n, 8, 4)
            if verbose:
                self.log.message('Divided {}x{} matrix into {} '
                                'batches of {} rows each.'.
                                format(n, n, nr_batches, take_rows), flush=True)
            i = 0
            # work on submatrices, trying to minimize disk I/O
            for h in range(nr_batches):
                idx_start = h*take_rows
                idx_stop = (h+1)*take_rows
                if idx_stop > n:
                    idx_stop = n
                idx = np.arange(idx_start, idx_stop).astype(np.int)
                current_rows = np.copy(self.D[idx, :])
                current_cols = np.copy(self.D[:, idx])
                # fill diag with 0 (self distance = 0)
                for r, c in enumerate(idx):
                    current_rows[r, c] = 0
                for c, r in enumerate(idx):
                    current_cols[r, c] = 0 
                updated_rows = np.zeros_like(current_rows)
                # calculations on submatrix in memory
                for inner_row in range(len(idx)):
                    if verbose and ((i+1)%1000 == 0 or i+1==n):
                        self.log.message("MP_gaussi: {} of {}.".format(i+1, n), 
                                        flush=True)
                    j_idx = np.arange(i+1, n)
                    j_len = np.size(j_idx)
                    
                    if self.isSimilarityMatrix:
                        p1 = norm.cdf(current_rows[inner_row, j_idx], \
                                          np.tile(mu[i], (1, j_len)), \
                                          np.tile(sd[i], (1, j_len)))        
                        p2 = norm.cdf(current_cols[j_idx, inner_row].T, \
                                          mu[j_idx], \
                                          sd[j_idx])
                        updated_rows[inner_row, j_idx] = (p1 * p2).ravel()
                    else:
                        p1 = 1 - norm.cdf(current_rows[inner_row, j_idx], \
                                          np.tile(mu[i], (1, j_len)), \
                                          np.tile(sd[i], (1, j_len)))        
                        p2 = 1 - norm.cdf(current_cols[j_idx, inner_row].T, \
                                          mu[j_idx], \
                                          sd[j_idx])
                        updated_rows[inner_row, j_idx] = (1 - p1 * p2).ravel()
                    i += 1
                # writing changes for many rows at once
                Dmp[idx, :] = updated_rows
            
            # make symmetric distance matrix
            #Dmp += Dmp.T # inefficient
            # hopefully faster using submatrices:
            for h in range(nr_batches):
                if verbose:
                    self.log.message('MP finalization: batch {}/{}.'.
                                     format(h, nr_batches-1), flush=True)
                row_start = h*take_rows
                row_stop = (h+1)*take_rows
                if row_stop > n:
                    row_stop = n
                row_idx = np.arange(row_start, row_stop).astype(np.int)
                col_start = row_start
                col_stop = n
                col_idx = np.arange(col_start, col_stop).astype(np.int)
                
                current_rows = np.copy(Dmp[np.ix_(row_idx, col_idx)])
                current_cols = np.copy(Dmp[np.ix_(col_idx, row_idx)])
                # mirroring the matrix
                current_cols += current_rows.T
                Dmp[np.ix_(col_idx, row_idx)] = current_cols 
            
            # need to set self value in case of similarity matrix
            if self.isSimilarityMatrix:
                np.fill_diagonal(Dmp, self.self_value)
    
        else: # work in memory
            Dmp = np.zeros_like(self.D)
            for i in range(n):
                if verbose and ((i+1)%1000 == 0 or i+1==n):
                    self.log.message("MP_gaussi: {} of {}."
                                     .format(i+1, n), flush=True)
                j_idx = np.arange(i+1, n)
                j_len = np.size(j_idx)
                
                if self.isSimilarityMatrix:
                    p1 = norm.cdf(self.D[i, j_idx], \
                                  np.tile(mu[i], (1, j_len)), \
                                  np.tile(sd[i], (1, j_len)))
                    p2 = norm.cdf(self.D[j_idx, i].T, \
                                  mu[j_idx], \
                                  sd[j_idx])
                    Dmp[i, i] = self.self_value
                    Dmp[i, j_idx] = (p1 * p2).ravel()
                else:
                    p1 = 1 - norm.cdf(self.D[i, j_idx], \
                                      np.tile(mu[i], (1, j_len)), \
                                      np.tile(sd[i], (1, j_len)))
                    p2 = 1 - norm.cdf(self.D[j_idx, i].T, \
                                      mu[j_idx], \
                                      sd[j_idx])
                    Dmp[i, j_idx] = (1 - p1 * p2).ravel()
                    
                Dmp[j_idx, i] = Dmp[i, j_idx]
    
        return Dmp

    def _partial_mp_gammai_sparse(self, batch, matrix, idx, n, A, B, verbose):
        Dmp = lil_matrix((len(batch), self.D.shape[1]), dtype=np.float32)
        
        for i, b in enumerate(batch):
            #print("i:", i, "b:", b)
            if verbose and ((batch[i]+1)%1000 == 0 or batch[i]+1==n or i==len(batch)-1 or i==0):
                self.log.message("MP_gammai_sparse: {} of {}. On {}.".format(
                                batch[i]+1, n, mp.current_process().name, flush=True))  # @UndefinedVariable
            j_idx = np.arange(b+1, n)
            j_len = np.size(j_idx)
             
            Dij = matrix[i, j_idx].toarray().ravel() #Extract dense rows temporarily
            Dji = matrix[j_idx, i].toarray().ravel() #for vectorization below.
            
            p1 = self.local_gamcdf(Dij, \
                                   np.tile(A[b], (1, j_len)), \
                                   np.tile(B[b], (1, j_len)))
            del Dij
            
            p2 = self.local_gamcdf(Dji, 
                                   A[j_idx], 
                                   B[j_idx])
            del Dji

            Dmp[i, j_idx] = (p1 * p2).ravel()     
            #need to mirror later!!   
        
        return batch, Dmp

    def mp_gammai_sparse(self, train_set_mask, verbose, n_jobs=-1):
        self.log.error("MP gammai sparse parallel implementation BROKEN!")
        # mean, variance WITH zero values
        #=======================================================================
        # from sklearn.utils.sparsefuncs_fast import csr_mean_variance_axis0  # @UnresolvedImport
        # mu, va = csr_mean_variance_axis0(self.D[train_set_mask])
        #=======================================================================
        
        # mean, variance WITHOUT zero values (missing values)
        # TODO implement train_test split
        mu = np.array(self.D.sum(0) / self.D.getnnz(0)).ravel()
        X = self.D.copy()
        X.data **= 2
        E1 = np.array(X.sum(0) / X.getnnz(0)).ravel()
        del X
        va = E1 - mu**2
        del E1
        
        A = (mu**2) / va
        B = va / mu
        del mu, va
        A[A<0] = np.nan
        B[B<=0] = np.nan

        Dmp = lil_matrix(self.D.shape, dtype=np.float32)
        n = self.D.shape[0]
        
        # Parallelization
        if n_jobs == -1: # take all cpus
            NUMBER_OF_PROCESSES = mp.cpu_count()  # @UndefinedVariable
        else:
            NUMBER_OF_PROCESSES = n_jobs
        tasks = []
        
        batches = _get_weighted_batches(n, NUMBER_OF_PROCESSES)
        
        for idx, batch in enumerate(batches):
            matrix = self.D.copy()
            tasks.append((self._partial_mp_gammai_sparse, (batch, matrix, idx, n, A, B, verbose)))   
        
        task_queue = mp.Queue()     # @UndefinedVariable
        done_queue = mp.Queue()     # @UndefinedVariable
        
        for task in tasks:
            task_queue.put(task)
            
        for i in range(NUMBER_OF_PROCESSES):  # @UnusedVariable
            mp.Process(target=_worker, args=(task_queue, done_queue)).start()  # @UndefinedVariable
        
        for i in range(len(tasks)):  # @UnusedVariable
            rows, Dmp_part = done_queue.get()
            task_queue.put('STOP')
            if verbose:
                self.log.message("Merging submatrix {} (rows {}..{})".format(i, rows[0], rows[-1]), flush=True)
            Dmp[rows, :] = Dmp_part
            
        for i in range(NUMBER_OF_PROCESSES):  # @UnusedVariable
            if verbose:
                self.log.message("Finalizing MP process {}".format(i), flush=True)
            task_queue.put('STOP')
         
        import os
        folder = '/tmp/feldbauer/hubPDBs2/'
        filename = 'D_mpgammai_triu'
        os.makedirs(folder, exist_ok=True)
        from scipy import io
        if verbose:
            self.log.message("Saving Dmp upper to {} as {}".format(folder, filename), flush=True)
        io.mmwrite(folder + filename, Dmp)
        
        Dmp = Dmp.tolil()
        if verbose:
            self.log.message("Mirroring distance matrix", flush=True)
        Dmp += Dmp.T
        
        if verbose:
            self.log.message("Setting self distances", flush=True)
        for i in range(Dmp.shape[0]):
            Dmp[i, i] = self.self_value

        if verbose:
            self.log.message("Converting to CSR matrix", flush=True)
        return Dmp.tocsr()
    
    def mp_gammai(self, train_set_mask=None, verbose=False, n_jobs=-1):
        """Compute Mutual Proximity modeled with independent Gamma distributions."""
        
        if verbose:
            self.log.message('Mutual proximity rescaling started.', flush=True)
            
        
        if not issparse(self.D):
            np.fill_diagonal(self.D, self.self_value)
        else:
            return self.mp_gammai_sparse(train_set_mask, verbose, n_jobs=n_jobs)
        
        mu = np.mean(self.D[train_set_mask], 0)
        va = np.var(self.D[train_set_mask], 0, ddof=1)
        A = (mu**2) / va
        B = va / mu
        
        if isinstance(self.D, np.memmap):
            from tempfile import mkstemp
            filename = mkstemp(suffix='pytmp')[1] # [0]... fd, [1]... filename
            self.log.message("Writing rescaled distance matrix to file:", filename)
            Dmp = np.memmap(filename, dtype='float64', mode='w+', shape=self.D.shape)
        else:
            Dmp = np.zeros_like(self.D)
        n = np.size(self.D, 0)
        
        for i in range(n):
            if verbose and ((i+1)%1000 == 0 or i+1==n):
                self.log.message("MP_gammai: {} of {}".format(i+1, n), flush=True)
            j_idx = np.arange(i+1, n)
            j_len = np.size(j_idx)
            
            if self.isSimilarityMatrix:
                p1 = self.local_gamcdf(self.D[i, j_idx], \
                                       np.tile(A[i], (1, j_len)), \
                                       np.tile(B[i], (1, j_len)))
                p2 = self.local_gamcdf(self.D[j_idx, i].T, 
                                       A[j_idx], 
                                       B[j_idx])
                Dmp[i, i] = self.self_value
                Dmp[i, j_idx] = (p1 * p2).ravel()
            else:
                p1 = 1 - self.local_gamcdf(self.D[i, j_idx], \
                                           np.tile(A[i], (1, j_len)), \
                                           np.tile(B[i], (1, j_len)))
                p2 = 1 - self.local_gamcdf(self.D[j_idx, i].T, 
                                           A[j_idx], 
                                           B[j_idx])
                Dmp[i, j_idx] = (1 - p1 * p2).ravel()
                
            Dmp[j_idx, i] = Dmp[i, j_idx]               
        
        return Dmp
    
    def local_gamcdf(self, x, a, b):
        a[a<0] = np.nan
        b[b<=0] = np.nan
        x[x<0] = 0
        z = x / b
        p = gammainc(a, z)
        return p
    
if __name__ == '__main__':
    """Test mp empiric similarity sparse sequential & parallel implementations"""
    from scipy.sparse import rand, csr_matrix
    D = rand(5000, 5000, 0.05, 'csr', np.float32, 42)
    D = np.triu(D.toarray())
    D = D + D.T
    np.fill_diagonal(D, 1)
    D = csr_matrix(D)
    from hub_toolbox import Hubness 
    from hub_toolbox import MutualProximity as MutProx
    h = Hubness.Hubness(D, 5, True)
    Sn, _, _ = h.calculate_hubness()
    print("Hubness:", Sn)
    #===========================================================================
    # mp1 = MutProx.MutualProximity(D, True)
    # Dmp1 = mp1.calculate_mutual_proximity(MutProx.Distribution.gammai, None, True, False, 0, None, empspex=True)
    # h = Hubness.Hubness(Dmp1, 5, True)
    # Sn, _, _ = h.calculate_hubness()
    # print("Hubness (sequential):", Sn)
    #===========================================================================
    mp2 = MutualProximity(D, True)
    Dmp2 = mp2.calculate_mutual_proximity(Distribution.empiric, None, True, False, 0, None, empspex=False, n_jobs=4)
    h = Hubness.Hubness(Dmp2, 5, True)
    Sn, _, _ = h.calculate_hubness()
    print("Hubness (parallel):", Sn)
    #print("Summed differences in the scaled matrices:", (Dmp1-Dmp2).sum())
    #print("D", D)
    #print("Dmp1", Dmp1)
    #print("Dmp2", Dmp2)
    