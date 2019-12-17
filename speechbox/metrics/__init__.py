import tensorflow as tf


class FalseNegativeRate(tf.keras.metrics.Metric):
    def __init__(self, thresholds, name="fnr", **kwargs):
        super().__init__(name=name, **kwargs)
        self.tp = tf.keras.metrics.TruePositives(thresholds=thresholds)
        self.fn = tf.keras.metrics.FalseNegatives(thresholds=thresholds)

    def update_state(self, y_true, y_pred, **kwargs):
        self.tp.update_state(y_true, y_pred, **kwargs)
        self.fn.update_state(y_true, y_pred, **kwargs)

    def reset_states(self):
        self.tp.reset_states()
        self.fn.reset_states()

    def result(self):
        tp = self.tp.result()
        fn = self.fn.result()
        return tf.math.divide_no_nan(fn, tp + fn)


class FalsePositiveRate(tf.keras.metrics.Metric):
    def __init__(self, thresholds, name="fpr", **kwargs):
        super().__init__(name=name, **kwargs)
        self.tn = tf.keras.metrics.TrueNegatives(thresholds=thresholds)
        self.fp = tf.keras.metrics.FalsePositives(thresholds=thresholds)

    def update_state(self, y_true, y_pred, **kwargs):
        self.tn.update_state(y_true, y_pred, **kwargs)
        self.fp.update_state(y_true, y_pred, **kwargs)

    def reset_states(self):
        self.tn.reset_states()
        self.fp.reset_states()

    def result(self):
        tn = self.tn.result()
        fp = self.fp.result()
        return tf.math.divide_no_nan(fp, tn + fp)


class AverageDetectionCost(tf.keras.metrics.Metric):
    """
    TensorFlow implementation of C_avg equation 32 from
    Li, H., Ma, B. and Lee, K.A., 2013. Spoken language recognition: from fundamentals to practice. Proceedings of the IEEE, 101(5), pp.1136-1159.
    https://ieeexplore.ieee.org/stamp/stamp.jsp?arnumber=6451097
    """
    def __init__(self, N, theta_det=(0.5,), C_miss=1.0, C_fa=1.0, P_tar=0.5, name="C_avg", **kwargs):
        super().__init__(name=name, **kwargs)
        self.P_miss = [FalseNegativeRate(theta_det) for _ in range(N)]
        self.P_fa = [[FalsePositiveRate(theta_det) for _ in range(N - 1)] for _ in range(N)]
        self.C_miss = C_miss
        self.C_fa = C_fa
        self.P_tar = P_tar

    def update_state(self, y_true, y_pred, **kwargs):
        """
        Given a batch of true labels and predicted labels, update P_miss and P_fa for each label.
        """
        # Update false negatives for each target l
        for l, fnr in enumerate(self.P_miss):
            fnr.update_state(y_true[:,l], y_pred[:,l], **kwargs)
        # Update false positives for each target l and non-target m pair (l, m), such that l != m
        for l, p_fa in enumerate(self.P_fa):
            for m, fpr in enumerate(p_fa):
                m += int(m >= l)
                fpr.update_state(y_true[:,m], y_pred[:,m], **kwargs)

    def reset_states(self):
        for fnr in self.P_miss:
            fnr.reset_states()
        for p_fa in self.P_fa:
            for fpr in p_fa:
                fpr.reset_states()

    def result(self):
        """
        Return final C_avg values for all thresholds as a 1D float tensor.
        To use different C_avg parameter values, set e.g. cavg.P_tar = 0.4 before calling cavg.result().
        """
        # FNR for each language, evaluated with all given thresholds
        P_miss = [fnr.result() for fnr in self.P_miss]
        # FPR for each target and non-target pair, evaluated with all given thresholds
        P_fa = [tf.reduce_mean([fpr.result() for fpr in p_fa], axis=0) for p_fa in self.P_fa]
        # Reduce mean over all languages
        avg_P_miss = tf.reduce_mean(P_miss, axis=0)
        avg_P_fa = tf.reduce_mean(P_fa, axis=0)
        return (
            self.C_miss * self.P_tar * avg_P_miss
            + self.C_fa * (1 - self.P_tar) * avg_P_fa
        )
