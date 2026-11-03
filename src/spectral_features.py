"""Shared numerical spectral coordinates; imported only by numerical workers."""
import numpy as np

FEATURES = ['log10Power1to40Hz', 'log10DeltaOverBeta', 'log10ThetaOverBeta',
            'log10AlphaOverBeta', 'log10HighBandOverBeta']
PSEUDOCOUNT = 1e-6


def feature_vector(window):
    f = window['features']
    shares = np.asarray(f['bandShares'])
    ratios = np.log10((shares[[0, 1, 2, 4]] + PSEUDOCOUNT) / (shares[3] + PSEUDOCOUNT))
    return [f['log10Power1to40Hz'], *ratios.tolist()]
