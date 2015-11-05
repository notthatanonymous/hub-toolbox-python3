"""
Performs a k-nearest neighbor classification experiment. If there is a
tie, the nearest neighbor determines the class

This file is part of the HUB TOOLBOX available at
http://ofai.at/research/impml/projects/hubology.html
(c) 2013, Dominik Schnitzer <dominik.schnitzer@ofai.at>

Usage:
  [acc, corr, cmat] = knn_classification(D, classes, k) - Use the distance
     matrix D (NxN) and the classes and perform a k-NN experiment. The
     classification accuracy is returned in acc. corr is a raw vector of the
     correctly classified items. cmat is the confusion matrix. 
     
This file was ported from MATLAB(R) code to Python3
by Roman Feldbauer <roman.feldbauer@ofai.at>

@author: Roman Feldbauer
@date: 2015-09-15
"""

import numpy as np

class KnnClassification():
    """Performs k-nearest neighbor classification.
    
    """
    
    def __init__(self, D, classes, k):
        self.D = np.copy(D)
        self.classes = np.copy(classes)
        if type(k) is np.ndarray:
            self.k = np.copy(k)
        else:
            self.k = np.array([k])
        
    def perform_knn_classification(self):
        """Performs k-nearest neighbor classification."""
        
        # Why would there be a need for more than one k?
        k_length = np.size(self.k)
            
        acc = np.zeros( (k_length, 1) )
        corr = np.zeros( (np.size(self.D, 0), k_length) )
        
        n = np.size(self.D, 1)
        
        cl = np.sort(np.unique(self.classes))
        cmat = np.zeros( (len(cl), len(cl)) )
        
        classes = self.classes
        for idx in range(len(cl)):
            classes[self.classes == cl[idx]] = idx
            
        cl = range(len(cl))
        
        for i in range(n):
            seed_class = classes[i]
            
            row = self.D[i, :]
            row[i] = np.inf
            
            # Randomize, in case there are several points of same distance
            # (this is especially relevant for SNN rescaling)
            rp = np.indices( (np.size(self.D, 1), ) )[0]
            rp = np.random.permutation(rp)
            d2 = row[rp]
            d2idx = np.argsort(d2, axis=0)
            idx = rp[d2idx]      
            
            # OLD code, non-randomized
            #idx = np.argsort(row)
            
            # More than one k?
            for j in range(k_length):
                nn_class = classes[idx[0:self.k[j]]]
                cs = np.bincount(nn_class.astype(int))
                max_cs = np.where(cs == np.max(cs))[0]
                
                # "tie": use nearest neighbor
                if len(max_cs) > 1:
                    if seed_class == nn_class[0]:
                        acc[j] += 1/n 
                        corr[i, j] = 1
                    cmat[seed_class, nn_class[0]] += 1       
                # majority vote
                else:
                    if cl[max_cs] == seed_class:
                        acc[j] += 1/n
                        corr[i, j] = 1
                    cmat[seed_class, cl[max_cs]] += 1
                           
        return acc, corr, cmat