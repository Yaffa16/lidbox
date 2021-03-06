import tensorflow as tf


@tf.function
def feature_scaling(X, min, max, axis=None):
    """Apply feature scaling on X over given axis such that all values are between [min, max]"""
    X_min = tf.math.reduce_min(X, axis=axis, keepdims=True)
    X_max = tf.math.reduce_max(X, axis=axis, keepdims=True)
    return min + (max - min) * tf.math.divide_no_nan(X - X_min, X_max - X_min)


@tf.function
def window_normalization(X, window_len=-1, normalize_variance=True):
    """
    Apply mean and variance normalization on batches of features matrices X with a given window length.
    By default normalize over whole tensor, i.e. wihtout a window.
    """
    tf.debugging.assert_rank(X, 3, message="Input to window_normalization should be of shape (batch_size, timedim, channels)")
    output = tf.identity(X)
    if window_len == -1 or tf.shape(X)[1] <= window_len:
        # All frames of X fit inside one window, no need for sliding window
        output = X - tf.math.reduce_mean(X, axis=1, keepdims=True)
        if normalize_variance:
            output = tf.math.divide_no_nan(output, tf.math.reduce_std(X, axis=1, keepdims=True))
    else:
        # Pad boundaries by reflecting at most half of the window contents from X, e.g.
        # Left pad [              X              ] right pad
        # 2, 1, 0, [ 0, 1, 2, ..., N-3, N-2, N-1 ] N-1, N-2, N-3, ...
        padding = tf.constant([[0, 0], [window_len//2, window_len//2 - 1 + (window_len&1)], [0, 0]])
        X_padded = tf.pad(X, padding, mode="REFLECT")
        windows = tf.signal.frame(X_padded, window_len, 1, axis=1)
        tf.debugging.assert_equal(tf.shape(windows)[1], tf.shape(X)[1], message="Mismatching amount of output windows and time steps in the input")
        output = X - tf.math.reduce_mean(windows, axis=2)
        if normalize_variance:
            output = tf.math.divide_no_nan(output, tf.math.reduce_std(windows, axis=2))
    return output


# Window normalization without padding
# NOTE tensorflow 2.1 does not support non-zero axes in tf.gather when indices are ragged so this was left out
# @tf.function
# def mean_var_norm_gather(X, window_len=300):
#     """Same as window_normalization but without padding."""
#     tf.debugging.assert_rank_at_least(X, 3)
#     num_total_frames = tf.shape(X)[1]
#     begin = tf.range(0, num_total_frames) - window_len // 2 + 1
#     end = begin + window_len
#     begin = tf.clip_by_value(begin, 0, num_total_frames)
#     end = tf.clip_by_value(end, 0, num_total_frames)
#     window_indices = tf.ragged.range(begin, end)
#     windows = tf.gather(X, window_indices, axis=1)
#     return tf.math.divide_no_nan(
#         X - tf.math.reduce_mean(windows, axis=2),
#         tf.math.reduce_std(windows, axis=2)
#     )


@tf.function
def window_normalization_numpy(X_t, window_len_t, normalize_variance_t):
    def f(X, window_len, normalize_variance):
        num_total_frames = X.shape[1]
        if num_total_frames <= window_len:
            centered = X - np.mean(X, axis=1, keepdims=True)
            if normalize_variance:
                centered /= np.std(X, axis=1, keepdims=True)
            return centered
        begin = np.arange(0, num_total_frames) - window_len // 2
        end = begin + window_len
        begin = np.clip(begin, 0, num_total_frames)
        end = np.clip(end, 0, num_total_frames)
        result = np.zeros_like(X)
        for i, (b, e) in enumerate(zip(begin, end)):
            window = X[:,b:e]
            centered = X[:,i] - np.mean(window, axis=1)
            if normalize_variance:
                centered /= np.std(window, axis=1)
            result[:,i] = centered
        return result
    return tf.numpy_function(f, [X_t, window_len_t, normalize_variance_t], tf.float32)
