import numpy as np
import pandas as pd
from sklearn.neighbors import LocalOutlierFactor
import matplotlib.pyplot as plt
from pathlib import Path
import streamlit as st
import os
from concurrent.futures import ProcessPoolExecutor, as_completed

# Local imports
from probabilistic_dca.config import N_INITS_DEFAULT, NUM_TRIALS_DEFAULT, SSE_THRESHOLD_DEFAULT, MIN_IMPROVEMENT_FRAC_DEFAULT, N_SAMPLES_DEFAULT, SEED, FORECAST_YEARS_DEFAULT, DAYS_PER_YEAR, TRAIN_PCT

from probabilistic_dca.logging_setup import setup_logger
from probabilistic_dca.my_dca_models.fitting import fit_single_sample

logger = setup_logger(__name__)

# from probabilistic_dca.my_dca_models.processing import load_data, remove_outliers, split_train_test

from probabilistic_dca.my_dca_models.data_processing import (
    data_processing, crossval_loess, rolling_std, sample_sorted_datasets
)
from probabilistic_dca.my_dca_models.fitting import (
    fit_model_for_samples_mstart_para, gather_sse_matrix, gather_predictions,
    forecast_from_params, compute_forecast_stats
)
from probabilistic_dca.my_dca_models.utilities import (
    calc_model_probabilities, compute_marginal_model_probs, rank_models_by_probability,
    combine_forecasts_across_models
)
from probabilistic_dca.my_dca_models.plotting import (
    generate_lof_plot, plot_model_predictions, plot_hindcast,
    plot_future_forecast, plot_post_prob_models, boxplot_eur
)
from probabilistic_dca.my_dca_models.models.arps_model import ArpsModel
from probabilistic_dca.my_dca_models.models.sem_model import SEMModel
from probabilistic_dca.my_dca_models.models.crm_model import CRMModel
from probabilistic_dca.my_dca_models.models.lgm_model import LGMModel

# Available models
MODEL_CLASSES = {
    "arps": ArpsModel,
    "sem": SEMModel,
    "crm": CRMModel,
    "lgm": LGMModel
}

# -------------------------
# Pipeline Functions
# -------------------------

def process_data(data_tbl, train_pct=TRAIN_PCT, frac_value=0.4, k_folds=10):
    logger.info(f"Starting Process Data")
    last_day, last_cum, x_train_i, models_df = data_processing(
        prod_df=data_tbl,
        train_pct=train_pct,
        frac_value=frac_value,
        plotting=True
    )
    best_window, best_span, models_df = crossval_loess(
        models_df, k_folds=k_folds, plotting=True
    )
    models_df = rolling_std(models_df, half_window=best_window, plotting=True)
    logger.info("Process Data completed")
    return last_day, last_cum, x_train_i, models_df


def montecarlo_sampling(models_df, n_samples=N_SAMPLES_DEFAULT):
    logger.info(f"Starting Monte Carlo sampling with {n_samples} samples")
    sample_sorted_df, sample_stats_df, sample_fig = sample_sorted_datasets(
        models_df, n_samples=n_samples, plotting=True
    )
    logger.info("Monte Carlo sampling completed")
    return sample_sorted_df, sample_stats_df, sample_fig

# fit_models --- parallel
# def fit_models(
#     train_df,
#     selected_models,
#     seed=SEED,
#     n_inits=N_INITS_DEFAULT,
#     num_trials=NUM_TRIALS_DEFAULT,
#     n_jobs=-1,
#     sse_threshold=SSE_THRESHOLD_DEFAULT,
#     min_improvement_frac=MIN_IMPROVEMENT_FRAC_DEFAULT,
#     status_placeholder=None
# ):
#     """
#     Fits each selected model in parallel with adaptive multi-start, returning a dict of results.
#     """    
#     logger.info(f"Starting Model Fitting with {n_inits} initializations and {num_trials} trials")
#     model_train_results = {}

#     for model_name in selected_models:
#         if status_placeholder:
#             # status_placeholder.info(f"🔧 Fitting **{model_name.upper()}** model...")
#             with status_placeholder.container() as ctr:
#                 ctr.markdown(f"🔧 Fitting **{model_name.upper()}**…")
#                 prog = ctr.progress(0)

#         model_class = MODEL_CLASSES[model_name]

#         fit_results = fit_model_for_samples_mstart_para(
#             model_class=model_class,
#             sample_df=train_df,
#             seed=seed,
#             n_inits=n_inits,
#             num_trials=num_trials,
#             use_shared_p50_init=False,
#             n_jobs=n_jobs,
#             sse_threshold=sse_threshold,
#             min_improvement_frac=min_improvement_frac,
#             progress_bar=prog,                # pass the Streamlit progress bar
#             total_samples=train_df.shape[1],  # number of sample_* columns
#         )

#         model_train_results[model_name] = fit_results

#     if status_placeholder:
#         status_placeholder.success("✅ Model fitting completed!")

#     logger.info("Model Fitting completed")
#     return model_train_results

# fit_models --- ThreadPoolExecutor
# from concurrent.futures import ThreadPoolExecutor, as_completed
# from probabilistic_dca.my_dca_models.fitting import fit_single_sample

# def fit_models(
#     train_df,
#     selected_models,
#     seed=SEED,
#     n_inits=N_INITS_DEFAULT,
#     num_trials=NUM_TRIALS_DEFAULT,
#     n_jobs=-1,
#     sse_threshold=SSE_THRESHOLD_DEFAULT,
#     min_improvement_frac=MIN_IMPROVEMENT_FRAC_DEFAULT,
#     status_placeholder=None,
#     progress_bar=None,    # new
# ):
#     """
#     Fits each selected model in parallel (ThreadPoolExecutor), updating
#     a Streamlit progress bar in the main thread.
#     """
#     logger.info(f"Starting Model Fitting with {n_inits} initializations and {num_trials} trials")
#     model_train_results = {}

#     for model_name in selected_models:
#         # 1) Build UI
#         if status_placeholder:
#             status_placeholder.markdown(f"🔧 Fitting **{model_name.upper()}**…")
#         # reuse the *other* slot for the bar:
#         prog = progress_bar if progress_bar is not None else st.empty().progress(0)

#         # 2) Prepare sample columns
#         sample_cols = [c for c in train_df.columns if c.startswith("sample_")]
#         total = len(sample_cols)

#         # 3) Launch ThreadPoolExecutor
#         results = []
#         with ThreadPoolExecutor(max_workers=n_jobs) as exe:
#             futures = {
#                 exe.submit(
#                     fit_single_sample,
#                     idx,
#                     col,
#                     MODEL_CLASSES[model_name],
#                     train_df,
#                     seed,
#                     n_inits,
#                     num_trials,
#                     False,           # use_shared_p50_init
#                     None,            # shared_initial_guess
#                     sse_threshold,
#                     min_improvement_frac,
#                 ): col
#                 for idx, col in enumerate(sample_cols)
#             }

#             # 4) As each future completes, collect its result and update the bar
#             for i, fut in enumerate(as_completed(futures), start=1):
#                 col, params, sse, pred, solver, stop_reason = fut.result()
#                 results.append((col, params, sse, pred, solver, stop_reason))
#                 if prog:
#                     prog.progress(i / total)

#         # 5) Unpack into your usual dict
#         param_list, sse_list, predictions, solver_list, early_stop_list = [], [], {}, [], []
#         for col, params, sse, pred, solver, stop_reason in results:
#             param_list.append(params)
#             sse_list.append(sse)
#             predictions[col] = pred
#             solver_list.append(solver)
#             early_stop_list.append(stop_reason)

#         df_params = np.array(param_list)
#         # turn into DataFrame with index=sample_cols
#         import pandas as pd
#         df_params = pd.DataFrame(param_list, index=sample_cols)

#         model_train_results[model_name] = {
#             "params": df_params,
#             "sse": np.array(sse_list),
#             "predictions": predictions,
#             "solver_used": solver_list,
#             "early_stop": early_stop_list,
#         }

#     if status_placeholder:
#         status_placeholder.success("✅ Model fitting completed!")

#     logger.info("Model Fitting completed")
#     return model_train_results


def fit_models(
    train_df,
    selected_models,
    seed=SEED,
    n_inits=N_INITS_DEFAULT,
    num_trials=NUM_TRIALS_DEFAULT,
    n_jobs=-1,
    sse_threshold=SSE_THRESHOLD_DEFAULT,
    min_improvement_frac=MIN_IMPROVEMENT_FRAC_DEFAULT,
    status_placeholder=None,  # text slot
    progress_bar=None         # bar slot
):
    """
    Fits each selected model in parallel (ProcessPoolExecutor),
    updating two Streamlit slots (text + bar) in the main thread.
    """
    logger.info(f"Starting Model Fitting with {n_inits} inits, {num_trials} trials")
    results_by_model = {}

    # remap -1 → all cores
    max_workers = os.cpu_count() if n_jobs < 1 else n_jobs

    for model_name in selected_models:
        # 1) update text
        if status_placeholder:
            status_placeholder.markdown(f"🔧 Fitting **{model_name.upper()}**…")

        # 2) grid of sample columns
        sample_cols = [c for c in train_df.columns if c.startswith("sample_")]
        total = len(sample_cols)

        # 3) spin up a process pool
        futures = []
        with ProcessPoolExecutor(max_workers=max_workers) as exe:
            for idx, col in enumerate(sample_cols):
                futures.append(
                    exe.submit(
                        fit_single_sample,
                        idx, col,
                        MODEL_CLASSES[model_name],
                        train_df,
                        seed,
                        n_inits,
                        num_trials,
                        False,    # use_shared_p50_init
                        None,     # shared_initial_guess
                        sse_threshold,
                        min_improvement_frac
                    )
                )

            # 4) collect & update bar in main thread
            completed = []
            for i, fut in enumerate(as_completed(futures), start=1):
                col, params, sse, pred, solver, stop = fut.result()
                completed.append((col, params, sse, pred, solver, stop))
                if progress_bar:
                    progress_bar.progress(i / total)

        # 5) unpack to your usual dict
        param_list, sse_list, preds, solvers, stops = [], [], {}, [], []
        for col, params, sse, pred, solver, stop in completed:
            param_list.append(params)
            sse_list.append(sse)
            preds[col] = pred
            solvers.append(solver)
            stops.append(stop)

        df_params = pd.DataFrame(param_list, index=sample_cols)
        results_by_model[model_name] = {
            "params":      df_params,
            "sse":         np.array(sse_list),
            "predictions": preds,
            "solver_used": solvers,
            "early_stop":  stops,
        }

    if status_placeholder:
        status_placeholder.success("✅ Model fitting completed!")

    logger.info("Model Fitting completed")
    return results_by_model

def analyze_train_fits(train_df, model_train_results, selected_models):
    train_plots = {}
    for model_name in selected_models:
        fit_results = model_train_results.get(model_name)
        if fit_results:
            pred_matrix = gather_predictions(fit_results, train_df)
            train_stats = compute_forecast_stats(pred_matrix)
            plot = plot_model_predictions(train_df, train_stats)
            train_plots[model_name] = plot
    logger.info(f"Train plots keys: {list(train_plots.keys())}")
    return train_plots


def calculate_model_probabilities(model_train_results, selected_models):
    logger.info(f"Starting Model Probabilities Calculation")
    model_dict = {name: {"sse": result["sse"], "forecast": result["predictions"]}
                  for name, result in model_train_results.items()}
    sse_matrix = gather_sse_matrix(model_dict, selected_models)
    prob_matrix = calc_model_probabilities(sse_matrix)
    marginal_probs = compute_marginal_model_probs(prob_matrix, selected_models)
    ranked_models = rank_models_by_probability(marginal_probs)
    prob_plot = plot_post_prob_models(ranked_models)
    logger.info("Model Probabilities Calculation completed")
    return prob_matrix, ranked_models, prob_plot


def hindcast_test(test_df, model_train_results, selected_models):
    logger.info(f"Starting Hindcast Test")
    hindcast_plots = {}
    t_test = test_df['x'].values

    for model_name in selected_models:
        fit_results = model_train_results.get(model_name)
        if fit_results:
            model_class = MODEL_CLASSES[model_name]
            forecast_matrix = forecast_from_params(model_class, fit_results["params"], t_test)
            forecast_stats = compute_forecast_stats(forecast_matrix)
            plot = plot_hindcast(test_df, forecast_stats)
            hindcast_plots[model_name] = plot
        else:
            logger.warning(f"No fit results found for model: {model_name}")

    logger.info(f"Hindcast plots generated for models: {list(hindcast_plots.keys())}")
    return hindcast_plots


def future_forecast(last_day, last_cum, model_train_results, selected_models, forecast_days=FORECAST_YEARS_DEFAULT*DAYS_PER_YEAR):
    logger.info(f"Starting Future Forecast for {forecast_days} days")
    future_forecasts = {}
    model_cum_stats = {}
    model_eur_stats = {}
    forecast_plots = {}  # <-- add this

    t_future = np.arange(last_day + 1, last_day + 1 + forecast_days)

    for model_name in selected_models:
        model_class = MODEL_CLASSES[model_name]
        fit_results = model_train_results[model_name]
        fc_matrix = forecast_from_params(model_class, fit_results["params"], t_future)
        fc_stats = compute_forecast_stats(fc_matrix)

        # Capture the figure
        fig = plot_future_forecast(t_future, fc_stats)
        forecast_plots[model_name] = fig

        cum_matrix = np.cumsum(fc_matrix, axis=0)
        cum_dist = cum_matrix[-1, :]
        cum_stats = {
            "p10": float(np.nanpercentile(cum_dist, 10)),
            "p25": float(np.nanpercentile(cum_dist, 25)),
            "p50": float(np.nanpercentile(cum_dist, 50)),
            "p75": float(np.nanpercentile(cum_dist, 75)),
            "p90": float(np.nanpercentile(cum_dist, 90)),
            "mean": float(np.nanmean(cum_dist))
        }
        eur_stats = {
            "p10": float(np.nanpercentile(cum_dist, 10) + last_cum),
            "p25": float(np.nanpercentile(cum_dist, 25) + last_cum),
            "p50": float(np.nanpercentile(cum_dist, 50) + last_cum),
            "p75": float(np.nanpercentile(cum_dist, 75) + last_cum),
            "p90": float(np.nanpercentile(cum_dist, 90) + last_cum),
            "mean": float(np.nanmean(cum_dist) + last_cum)
        }
        future_forecasts[model_name] = fc_matrix.T
        model_cum_stats[model_name] = cum_stats        
        model_eur_stats[model_name] = eur_stats

    logger.info("Future Forecast completed")
    return future_forecasts, model_cum_stats, model_eur_stats, forecast_plots  # <-- return it


def multi_model_combination(future_forecasts, prob_matrix, last_cum, selected_models):
    logger.info(f"Starting Multimodel Combination")
    model_arrays = [future_forecasts[model] for model in selected_models]
    forecast_tensor = np.stack(model_arrays, axis=0)
    combined_forecast = combine_forecasts_across_models(forecast_tensor, prob_matrix)
    cum_combined = np.cumsum(combined_forecast, axis=1)
    final_cum_samples = cum_combined[:, -1]

    combined_cum_stats = {
        "p10": float(np.nanpercentile(final_cum_samples, 10)),
        "p25": float(np.nanpercentile(final_cum_samples, 25)),
        "p50": float(np.nanpercentile(final_cum_samples, 50)),
        "p75": float(np.nanpercentile(final_cum_samples, 75)),
        "p90": float(np.nanpercentile(final_cum_samples, 90)),
        "mean": float(np.nanmean(final_cum_samples))
    }
    
    combined_eur_stats = {
        "p10": float(np.nanpercentile(final_cum_samples, 10) + last_cum),
        "p25": float(np.nanpercentile(final_cum_samples, 25) + last_cum),
        "p50": float(np.nanpercentile(final_cum_samples, 50) + last_cum),
        "p75": float(np.nanpercentile(final_cum_samples, 75) + last_cum),
        "p90": float(np.nanpercentile(final_cum_samples, 90) + last_cum),
        "mean": float(np.nanmean(final_cum_samples) + last_cum)
    }
        
    logger.info("Multimodel Combination completed")
    return combined_forecast, combined_cum_stats, combined_eur_stats

def generate_eur_boxplot(model_eur_stats, combined_eur_stats, selected_models):
    # Prepare list dynamically from selected models
    model_display_names = [m.upper() for m in selected_models]

    df_eur = pd.DataFrame({
        "model_name": model_display_names + ["Combined"],
        "y10":   [model_eur_stats[m]['p10'] for m in selected_models] + [combined_eur_stats["p10"]],
        "y25":   [model_eur_stats[m]['p25'] for m in selected_models] + [combined_eur_stats["p25"]],
        "y50":   [model_eur_stats[m]['p50'] for m in selected_models] + [combined_eur_stats["p50"]],
        "y75":   [model_eur_stats[m]['p75'] for m in selected_models] + [combined_eur_stats["p75"]],
        "y90":   [model_eur_stats[m]['p90'] for m in selected_models] + [combined_eur_stats["p90"]],
        "ymean": [model_eur_stats[m]['mean'] for m in selected_models] + [combined_eur_stats["mean"]]
    })

    fig = boxplot_eur(df_eur)
    return fig, df_eur


def prepare_fit_results_for_export(model_results):
    """
    Combine model fit results across all models into a single DataFrame,
    including model name, sample ID, parameters, solver used, SSE, and early stopping reason.
    """
    dfs = []

    for model_name, result in model_results.items():
        df_params = result["params"].copy()
        df_params["model"] = model_name

        # Use directly stored solver list
        if "solver_used" in result:
            df_params["last_solver"] = result["solver_used"]
        else:
            df_params["last_solver"] = "unknown"

        # Add SSE values if available
        if "sse" in result:
            df_params["sse"] = result["sse"]

        # Add early stop reason if available
        if "early_stop" in result:
            df_params["early_stop"] = result["early_stop"]

        dfs.append(df_params)

    df_combined = pd.concat(dfs).reset_index().rename(columns={"index": "sample_name"})
    df_combined["sample"] = df_combined["sample_name"].str.extract(r"sample_(\\d+)").astype(float)

    cols = ["model", "sample", "sample_name", "last_solver", "sse", "early_stop"] + [
        col for col in df_combined.columns if col not in ["model", "sample", "sample_name", "last_solver", "sse", "early_stop"]
    ]
    df_combined = df_combined[cols]

    return df_combined
