"""Statistics used with the probe pipeline, pure numpy (+ scipy for t quantiles and binomial CIs).

  perm     within_group_perms; freedman_lane_coef (Freedman-Lane within-group permutation test of one coefficient)
  decode   auc, auc_rows, pca_fit, ridge_fit, group_center, loco_scores (leave-one-group-out), fl_scores and
           incremental_auc (held-out ΔAUC of activations over a baseline, Freedman-Lane within-group p)
  fe       within (group fixed effects), cr2 (Bell-McCaffrey), ols_fe (CR2 SEs, t(G-1) CIs), iv_2sls (CR1, t(G-1))
  multi    holm, jackknife_groups
  calib    pass_rate (binomial CI that must cover alpha), simulate, mde

None of these decides anything: alpha, B, grouping and the claim rule are a project's pre-registered choices, and a
test is calibrated on the project's own design-shaped nulls (calib) before it is used.
"""
