# ---
# jupyter:
#   jupytext:
#     custom_cell_magics: kql
#     text_representation:
#       extension: .py
#       format_name: percent
#       format_version: '1.3'
#       jupytext_version: 1.11.2
#   kernelspec:
#     display_name: Python 3.9.7 ('gpjax')
#     language: python
#     name: python3
# ---

# %%
# %load_ext autoreload
# %autoreload 2

# %% [markdown]
# # Sparse Regression
#
# In this notebook we'll demonstrate how the sparse variational Gaussian process model of <strong data-cite="hensman2013gaussian"></strong>. When seeking to model more than ~5000 data points and/or the assumed likelihood is non-Gaussian, the sparse Gaussian process presented here will be a tractable option. However, for models of less than 5000 data points and a Gaussian likelihood function, we would recommend using the marginal log-likelihood approach presented in the [Regression notebook](https://gpjax.readthedocs.io/en/latest/nbs/regression.html).

# %%

import jax.numpy as jnp
import jax.random as jr
import matplotlib.pyplot as plt
import tensorflow as tf
from jax import jit
from jax.example_libraries import optimizers

import gpjax as gpx

key = jr.PRNGKey(123)

# %% [markdown]
# ## Data
#
# We'll simulate 5000 observation inputs $X$ and simulate the corresponding output $y$ according to
# $$y = \sin(4X) + \cos(2X)\,.$$
# We'll perturb our observed responses through a sequence independent and identically distributed draws from $\mathcal{0, 0.2}$.
#

# %%
N = 5000
noise = 0.2

x = jr.uniform(key=key, minval=-5.0, maxval=5.0, shape=(N,)).sort().reshape(-1, 1)
f = lambda x: jnp.sin(4 * x) + jnp.cos(2 * x)
signal = f(x)
y = signal + jr.normal(key, shape=signal.shape) * noise
xtest = jnp.linspace(-5.5, 5.5, 500).reshape(-1, 1)

# %% [markdown]
# ## Inducing points
#
# Tractability in a sparse Gaussian process is made possible through a set of inducing points $Z$. At a high-level, the set of inducing points acts as a pseudo-dataset that enables low-rank approximations $\mathbf{K}_{zz}$ of the true covariance matrix $\mathbf{K}_{xx}$ to be computed. More tricks involving a variational treatment of the model's marginal log-likelihood unlock the full power of sparse GPs, but more on that later. For now, we'll initialise a set of inducing points using a linear spaced grid across our observed data's support.

# %%
Z = jnp.linspace(-5.0, 5.0, 50).reshape(-1, 1)

# %%
fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(x, y, "o", alpha=0.3)
ax.plot(xtest, f(xtest))
ax.scatter(Z, jnp.zeros_like(Z), marker="|", color="black")
[ax.axvline(x=z, color="black", alpha=0.3, linewidth=1) for z in Z]
plt.show()

# %% [markdown]
# ## Defining processes
#
# Unlike regular GP regression, we won't ever acess the marginal log-likelihood of our true process. Instead, we'll introduce a variational approximation $q$ that is itself a Gaussian process. We'll then seek to minimise the Kullback-Leibler divergence $\operatorname{KL}(\cdot || \cdot)$ from our approximate process $q$ to the true process $p$ through the evidence lower bound.

# %%
D = gpx.Dataset(X=x, y=y)
likelihood = gpx.Gaussian(num_datapoints=N)
true_process = gpx.Prior(kernel=gpx.RBF()) * likelihood

q = gpx.VariationalGaussian(inducing_inputs=Z)

# %% [markdown]
# We collect our true and approximate posterior Gaussian processes up into an `SVGP` object. This object is simply there to define the variational strategy that we will adopt in the forthcoming inference.

# %%
svgp = gpx.SVGP(posterior=true_process, variational_family=q)

# %% [markdown]
# ## Inference
#
# ### Evidence lower bound
#
# With a model now defined, we will seek to infer the optimal model hyperparameters $\theta$ and the variational mean $\mathbf{m}$ and covariance $\mathbf{S}$ that define our approximate posterior. To achieve this, we will maximise the evidence lower bound with respect to $\{\theta, \mathbf{m}, \mathbf{S} \}$. This is a task that is equivalent to minimising the Kullback-Leibler divergence from the approximate posterior to the true posterior, up to a normalisation constant. For more details on this, see Sections 3.1 and 4.1 of the excellent review paper <strong data-cite="leibfried2020tutorial"></strong>.
#
# As we wish to maximise the ELBO, we'll return it's negative so that minimisation of the negative is equivalent to maximisation of the true ELBO.
#

# %%
params, trainables, constrainers, unconstrainers = gpx.initialise(svgp)
params = gpx.transform(params, unconstrainers)

loss_fn = jit(svgp.elbo(D, constrainers, negative=True))

# %% [markdown]
# ### Mini-batching
#
# Despite introducing a set of inducing points into our model, inference can still be intractable when the observed dataset's size is very large. To circumvent this, optimisation can be done using stochastic mini-batches. The `Dataset` object given in GPJax can easily be batched using the `batch()` method. Further accelerations can be given using prefetching and cacheing in a manner similar to [TensorFlow's Dataset object](https://www.tensorflow.org/guide/data_performance).

# %%
opt_init, opt_update, get_params = optimizers.adam(step_size=0.01)

Dbatched = D.cache().repeat().shuffle(D.n).batch(batch_size=64).prefetch(buffer_size=1)

learned_params = gpx.abstractions.fit_batches(
    objective = loss_fn,
    train_data = Dbatched, 
    params = params,
    trainables = trainables,
    opt_init = opt_init,
    opt_update = opt_update,
    get_params = get_params,
    n_iters=2500,
)
learned_params = gpx.transform(learned_params, constrainers)

# %% [markdown]
# ## Predictions
#
# With optimisation complete, we are free to use our inferred parameter set to make predictions on a test set of data. This can be achieve in an identical manner to all other GP models within GPJax.

# %%
latent_dist = svgp(learned_params)(xtest)
predictive_dist = likelihood(latent_dist, learned_params)

# %%
meanf = predictive_dist.mean()
sigma = predictive_dist.stddev()

# %%
fig, ax = plt.subplots(figsize=(12, 5))
ax.plot(x, y, "o", alpha=0.15, label="Training Data", color="tab:gray")
ax.plot(xtest, meanf, label="Posterior mean", color="tab:blue")
ax.fill_between(xtest.flatten(), meanf - sigma, meanf + sigma, alpha=0.3)
[
    ax.axvline(x=z, color="black", alpha=0.3, linewidth=1)
    for z in learned_params["variational_family"]["inducing_inputs"]
]
plt.show()

# %% [markdown]
# ## System information

# %%
# %reload_ext watermark
# %watermark -n -u -v -iv -w -a 'Thomas Pinder & Daniel Dodd'
