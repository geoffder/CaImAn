#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Thu Jul 12 11:11:45 2018

@author: epnevmatikakis
"""

import numpy as np
import matplotlib.pyplot as plt
import scipy.sparse
import caiman
import logging
from .utilities import detrend_df_f
from ...components_evaluation import (
        evaluate_components_CNN, estimate_components_quality_auto,
        select_components_from_metrics)
from .initialization import downscale

class Estimates(object):
    def __init__(self, A=None, b=None, C=None, f=None, R=None, dims=None):
        # variables related to the estimates of traces, footprints, deconvolution and background
        self.A = A
        self.C = C
        self.f = f
        self.b = b
        self.R = R
        self.W = None
        self.b0 = None
        self.YrA = None

        self.S = None
        self.sn = None
        self.g = None
        self.bl = None
        self.c1 = None
        self.neurons_sn = None
        self.lam = None

        self.center = None

        self.merged_ROIs = None
        self.coordinates = None
        self.F_dff = None

        self.idx_components = None
        self.idx_components_bad = None
        self.SNR_comp = None
        self.r_values = None
        self.cnn_preds = None

        # online

        self.noisyC = None
        self.C_on = None
        self.Ab = None
        self.Cf = None
        self.OASISinstances = None
        self.CY = None
        self.CC = None
        self.Ab_dense = None
        self.Yr_buf = None
        self.mn = None
        self.vr = None
        self.ind_new = None
        self.rho_buf = None
        self.AtA = None
        self.AtY_buf = None
        self.sv = None
        self.groups = None

        self.dims = dims
        self.shifts = []



    def plot_contours(self, img=None, idx=None, crd=None, thr_method='max',
                      thr='0.2'):
        """view contour plots for each spatial footprint. 
        Parameters:
        -----------
        img :   np.ndarray
                background image for contour plotting. Default is the mean
                image of all spatial components (d1 x d2)
        idx :   list
                list of accepted components

        crd :   list
                list of coordinates (if empty they are computed)

        thr_method : str
                     thresholding method for computing contours ('max', 'nrg')

        thr : float
                threshold value
        """
        if 'csc_matrix' not in str(type(self.A)):
            self.A = scipy.sparse.csc_matrix(self.A)
        if img is None:
            img = np.reshape(np.array(self.A.mean(1)), self.dims, order='F')
        if self.coordinates is None:  # not hasattr(self, 'coordinates'):
            self.coordinates = caiman.utils.visualization.get_contours(self.A, self.dims, thr=thr, thr_method=thr_method)
        plt.figure()
        if idx is None:
            caiman.utils.visualization.plot_contours(self.A, img, coordinates=self.coordinates)
        else:
            if not isinstance(idx, list):
                idx = idx.tolist()
            coor_g = [self.coordinates[cr] for cr in idx]
            bad = list(set(range(self.A.shape[1])) - set(idx))
            coor_b = [self.coordinates[cr] for cr in bad]
            plt.subplot(1, 2, 1)
            caiman.utils.visualization.plot_contours(self.A[:, idx], img,
                                                     coordinates=coor_g)
            plt.title('Accepted Components')
            bad = list(set(range(self.A.shape[1])) - set(idx))
            plt.subplot(1, 2, 2)
            caiman.utils.visualization.plot_contours(self.A[:, bad], img,
                                                     coordinates=coor_b)
            plt.title('Rejected Components')
        return self

    def view_components(self, Yr=None, img=None, idx=None):
        """view spatial and temporal components interactively

        Parameters:
        -----------
        Yr :    np.ndarray
                movie in format pixels (d) x frames (T)

        dims :  tuple
                dimensions of the FOV

        img :   np.ndarray
                background image for contour plotting. Default is the mean
                image of all spatial components (d1 x d2)

        idx :   list
                list of components to be plotted


        """
        if 'csc_matrix' not in str(type(self.A)):
            self.A = scipy.sparse.csc_matrix(self.A)
        if 'array' not in str(type(self.b)):
            self.b = self.b.toarray()

        plt.ion()
        nr, T = self.C.shape
        if self.R is None:
            self.R = self.YrA
        if self.R.shape != [nr, T]:
            if self.YrA is None:
                self.compute_residuals(Yr)
            else:
                self.R = self.YrA

        if img is None:
            img = np.reshape(np.array(self.A.mean(axis=1)), self.dims, order='F')

        if idx is None:
            caiman.utils.visualization.view_patches_bar(Yr, self.A, self.C,
                    self.b, self.f, self.dims[0], self.dims[1], YrA=self.R, img=img)
        else:
            caiman.utils.visualization.view_patches_bar(Yr, self.A.tocsc()[:,idx], 
                                                        self.C[idx], self.b, self.f, 
                                                        self.dims[0], self.dims[1], YrA=self.R[idx], img=img)

    def play_movie(self, imgs, q_max=99.75, q_min=2, gain_res=1,
                   magnification=1, include_bck=True,
                   frame_range=slice(None, None, None),
                   bpx=0):

        """Displays a movie with three panels (original data (left panel),
        reconstructed data (middle panel), residual (right panel))
        Parameters:
        -----------
        imgs: np.array (possibly memory mapped, t,x,y[,z])
            Imaging data

        q_max: float (values in [0, 100])
            percentile for maximum plotting value

        q_min: float (values in [0, 100])
            percentile for minimum plotting value

        gain_res: float
            amplification factor for residual movie

        magnification: float
            magnification factor for whole movie

        include_bck: bool
            flag for including background in original and reconstructed movie

        frame_rage: range or slice or list
            display only a subset of frames

        bpx: int
            number of pixels to exclude on each border

        Returns:
        --------
        self (to stop the movie press 'q')
        """
        dims = imgs.shape[1:]
        if 'movie' not in str(type(imgs)):
            imgs = caiman.movie(imgs)
        Y_rec = self.A.dot(self.C[:, frame_range])
        Y_rec = Y_rec.reshape(dims + (-1,), order='F')
        Y_rec = Y_rec.transpose([2, 0, 1])
        if self.b is not None and self.f is not None:
            B = self.b.dot(self.f[:, frame_range])
            if 'matrix' in str(type(B)):
                B = B.toarray()
            B = B.reshape(dims + (-1,), order='F').transpose([2, 0, 1])
        elif self.W is not None:
            ssub_B = int(round(np.sqrt(np.prod(dims) / self.W.shape[0])))
            B = imgs[frame_range].reshape((-1, np.prod(dims)), order='F').T - \
                self.A.dot(self.C[:, frame_range])
            if ssub_B==1:
                B = self.b0[:, None] + self.W.dot(B - self.b0[:, None])
            else:
                B = self.b0[:, None] + (np.repeat(np.repeat(self.W.dot(
                    downscale(B.reshape(dims + (B.shape[-1],), order='F'),
                              (ssub_B, ssub_B, 1)).reshape((-1, B.shape[-1]), order='F') -
                    downscale(self.b0.reshape(dims, order='F'),
                              (ssub_B, ssub_B)).reshape((-1, 1), order='F'))
                    .reshape(((dims[0] - 1) // ssub_B + 1, (dims[1] - 1) // ssub_B + 1, -1), order='F'),
                    ssub_B, 0), ssub_B, 1)[:dims[0], :dims[1]].reshape((-1, B.shape[-1]), order='F'))
            B = B.reshape(dims + (-1,), order='F').transpose([2, 0, 1])
        else:
            B = np.zeros_like(Y_rec)
        if bpx > 0:
            B = B[:, bpx:-bpx, bpx:-bpx]
            Y_rec = Y_rec[:, bpx:-bpx, bpx:-bpx]
            imgs = imgs[:, bpx:-bpx, bpx:-bpx]

        Y_res = imgs[frame_range] - Y_rec - B

        caiman.concatenate((imgs[frame_range] - (not include_bck)*B, Y_rec + include_bck*B, Y_res*gain_res), axis=2).play(q_min=q_min, q_max=q_max, magnification=magnification)

        return self

    def compute_residuals(self, Yr):
        """compute residual for each component (variable R)

         Parameters:
         -----------
         Yr :    np.ndarray
                 movie in format pixels (d) x frames (T)

        """
        if len(Yr.shape) > 2:
            Yr = np.reshape(Yr.transpose(1,2,0), (-1, Yr.shape[0]), order='F')
        if 'csc_matrix' not in str(type(self.A)):
            self.A = scipy.sparse.csc_matrix(self.A)
        if 'array' not in str(type(self.b)):
            self.b = self.b.toarray()
        if 'array' not in str(type(self.C)):
            self.C = self.C.toarray()
        if 'array' not in str(type(self.f)):
            self.f = self.f.toarray()

        Ab = scipy.sparse.hstack((self.A, self.b)).tocsc()
        nA2 = np.ravel(Ab.power(2).sum(axis=0))
        nA2_inv_mat = scipy.sparse.spdiags(
            1. / nA2, 0, nA2.shape[0], nA2.shape[0])
        Cf = np.vstack((self.C, self.f))
        if 'numpy.ndarray' in str(type(Yr)):
            YA = (Ab.T.dot(Yr)).T * nA2_inv_mat
        else:
            YA = caiman.mmapping.parallel_dot_product(Yr, Ab, dview=self.dview,
                        block_size=2000, transpose=True, num_blocks_per_run=5) * nA2_inv_mat

        AA = Ab.T.dot(Ab) * nA2_inv_mat
        self.R = (YA - (AA.T.dot(Cf)).T)[:, :self.A.shape[-1]].T

        return self

    def detrend_df_f(self, quantileMin=8, frames_window=500,
                     flag_auto=True, use_fast=False, use_residuals=True):
        """Computes DF/F normalized fluorescence for the extracted traces. See
        caiman.source.extraction.utilities.detrend_df_f for details

        Parameters:
        -----------
        quantile_min: float
            quantile used to estimate the baseline (values in [0,100])

        frames_window: int
            number of frames for computing running quantile

        flag_auto: bool
            flag for determining quantile automatically (different for each
            trace)

        use_fast: bool
            flag for using approximate fast percentile filtering

        use_residuals: bool
            flag for using non-deconvolved traces in DF/F calculation

        Returns:
        --------
        self: CNMF object
            self.F_dff contains the DF/F normalized traces
        """

        if self.C is None:
            logging.warning("There are no components for DF/F extraction!")
            return self

        if use_residuals:
            if self.R is None:
                if self.YrA is None:
                    R = None
                else:
                    R = self.YrA
            else:
                R = self.R
        else:
            R = None

        self.F_dff = detrend_df_f(self.A, self.b, self.C, self.f, R,
                                  quantileMin=quantileMin,
                                  frames_window=frames_window,
                                  flag_auto=flag_auto, use_fast=use_fast)
        return self

    def normalize_components(self):
        """ Normalizes components such that spatial components have l_2 norm 1
        """
        if 'csc_matrix' not in str(type(self.A)):
            self.A = scipy.sparse.csc_matrix(self.A)
        if 'array' not in str(type(self.C)):
            self.C = self.C.toarray()
        if 'array' not in str(type(self.f)) and self.f is not None:
            self.f = self.f.toarray()

        nA = np.sqrt(np.ravel(self.A.power(2).sum(axis=0)))
        nA_mat = scipy.sparse.spdiags(nA, 0, nA.shape[0], nA.shape[0])
        nA_inv_mat = scipy.sparse.spdiags(1. / nA, 0, nA.shape[0], nA.shape[0])
        self.A = self.A * nA_inv_mat
        self.C = nA_mat * self.C
        if self.YrA is not None:
            self.YrA = nA_mat * self.YrA
        if self.R is not None:
            self.R = nA_mat * self.R
        if self.bl is not None:
            self.bl = nA * self.bl
        if self.c1 is not None:
            self.c1 = nA * self.c1
        if self.neurons_sn is not None:
            self.neurons_sn = nA * self.neurons_sn

        if self.f is not None:  # 1p with exact ring-model
            nB = np.sqrt(np.ravel((self.b.power(2) if scipy.sparse.issparse(self.b)
                         else self.b**2).sum(axis=0)))
            nB_mat = scipy.sparse.spdiags(nB, 0, nB.shape[0], nB.shape[0])
            nB_inv_mat = scipy.sparse.spdiags(1. / nB, 0, nB.shape[0], nB.shape[0])
            self.b = self.b * nB_inv_mat
            self.f = nB_mat * self.f
        return self

    def select_components(self, idx_components=None, use_object=False):
        """Keeps only a selected subset of components and removes the rest.
        The subset can be either user defined with the variable idx_components
        or read from the estimates object. The flag use_object determines this
        choice. If no subset is present then all components are kept.
        Parameters:
        -----------
        idx_components: list
            indeces of components to be kept

        use_object: bool
            Flag to use self.idx_components for reading the indeces.

        Returns:
        --------
        self: Estimates object
        """
        if use_object:
            idx_components = self.idx_components
        if idx_components is None:
            idx_components = range(self.A.shape[-1])

        self.A = self.A.tocsc()[:, idx_components]
        self.C = self.C[idx_components]
        self.S = self.S[idx_components]
        self.YrA = self.YrA[idx_components]
        self.R = self.YrA
        self.g = self.g[idx_components]
        self.bl = self.bl[idx_components]
        self.c1 = self.c1[idx_components]
        self.neurons_sn = self.neurons_sn[idx_components]
        self.lam = self.lam[idx_components]
        self.idx_components = None
        return self

    def evaluate_components_CNN(self, params, neuron_class=1):
        """Estimates the quality of inferred spatial components using a
        pretrained CNN classifier.
        Parameters:
        -----------
        params: params object
            see .params for details
        neuron_class: int
            class label for neuron shapes
        Returns:
        ----------
        self: Estimates object
            self.idx_components contains the indeced of components above
            the required treshold.
        """
        dims = params.get('data', 'dims')
        gSig = params.get('init', 'gSig')
        min_cnn_thr= params.get('quality', 'min_cnn_thr')
        predictions = evaluate_components_CNN(self.A, dims, gSig)[0]
        self.cnn_preds = predictions[:, neuron_class]
        self.idx_components = np.where(self.cnn_preds >= min_cnn_thr)[0]
        return self
    
    def evaluate_components(self, imgs, params, dview=None):
        """Computes the quality metrics for each component and stores the
        indeces of the components that pass user specified thresholds. The
        various thresholds and parameters can be passed as inputs. If left
        empty then they are read from self.params.quality']
        Parameters:
        -----------
        imgs: np.array (possibly memory mapped, t,x,y[,z])
            Imaging data

        fr: float
            Imaging rate

        decay_time: float
            length of decay of typical transient (in seconds)

        min_SNR: float
            trace SNR threshold

        rval_thr: float
            space correlation threshold

        use_cnn: bool
            flag for using the CNN classifier

        min_cnn_thr: float
            CNN classifier threshold

        Returns:
        --------
        self: CNMF object
            self.idx_components: np.array
                indeces of accepted components
            self.idx_components_bad: np.array
                indeces of rejected components
            self.SNR_comp: np.array
                SNR values for each temporal trace
            self.r_values: np.array
                space correlation values for each component
            self.cnn_preds: np.array
                CNN classifier values for each component
        """
        dims = imgs.shape[1:]
        opts = params.get_group('quality')
        idx_components, idx_components_bad, SNR_comp, r_values, cnn_preds = \
        estimate_components_quality_auto(imgs, self.A, self.C, self.b, self.f,
                                         self.YrA, 
                                         params.get('data', 'fr'),
                                         params.get('data', 'decay_time'),
                                         params.get('init', 'gSig'),
                                         dims, dview=dview,
                                         min_SNR=opts['min_SNR'],
                                         r_values_min=opts['rval_thr'],
                                         use_cnn=opts['use_cnn'],
                                         thresh_cnn_min=opts['min_cnn_thr'])
        self.idx_components = idx_components
        self.idx_components_bad = idx_components_bad
        self.SNR_comp = SNR_comp
        self.r_values = r_values
        self.cnn_preds = cnn_preds

        return self


    def filter_components(self, imgs, **kwargs):
        """Filters components based on given thresholds without re-computing
        the quality metrics. If the quality metrics are not present then it
        calls self.evaluate components.
        Parameters:
        -----------
        imgs: np.array (possibly memory mapped, t,x,y[,z])
            Imaging data

        fr: float
            Imaging rate

        decay_time: float
            length of decay of typical transient (in seconds)

        min_SNR: float
            trace SNR threshold

        SNR_lowest: float
            minimum required trace SNR

        rval_thr: float
            space correlation threshold

        rval_lowest: float
            minimum required space correlation

        use_cnn: bool
            flag for using the CNN classifier

        min_cnn_thr: float
            CNN classifier threshold

        cnn_lowest: float
            minimum required CNN threshold

        gSig_range: list
            gSig scale values for CNN classifier

        Returns:
        --------
        self: CNMF object
            self.idx_components: np.array
                indeces of accepted components
            self.idx_components_bad: np.array
                indeces of rejected components
            self.SNR_comp: np.array
                SNR values for each temporal trace
            self.r_values: np.array
                space correlation values for each component
            self.cnn_preds: np.array
                CNN classifier values for each component
        """
        dims = imgs.shape[1:]
        self.params.set('quality', kwargs)

        opts = self.params.get_group('quality')
        self.idx_components, self.idx_components_bad, self.cnn_preds = \
        select_components_from_metrics(self.A, dims, self.params.get('init', 'gSig'), self.r_values,
                                       self.SNR_comp, predictions=self.cnn_preds,
                                       r_values_min=opts['rval_thr'],
                                       r_values_lowest=opts['rval_lowest'],
                                       min_SNR=opts['min_SNR'],
                                       min_SNR_reject=opts['SNR_lowest'],
                                       thresh_cnn_min=opts['min_cnn_thr'],
                                       thresh_cnn_lowest=opts['cnn_lowest'],
                                       use_cnn=opts['use_cnn'],
                                       gSig_range=opts['gSig_range'])

        return self