# Item 6b offline diagnostics (descriptive; $0)

**STOP flag (|cos(pattern, homogeneous n direction)| > the isotropic magnitude null at any site): True**

## lottery_L38 (primary)

Cosines (columns: reference directions; nulls are 99th percentiles of |cos| with that reference):

| vector | n_homog_below | n_homog_above | n_slope_all | mod_grid_matched_clean | mod_frame_matched | probe_clean |
|---|---|---|---|---|---|---|
| *null iso / cov* | 0.035 / 0.806 | 0.037 / 0.458 | 0.032 / 0.962 | 0.036 / 0.704 | 0.031 / 0.955 | |
| probe_clean | 0.019 | 0.095 | 0.101 | 0.175 | 0.116 | 1.000 |
| pattern | 0.658 | 0.037 | 0.968 | 0.375 | 0.984 | 0.127 |
| pattern_raw | 0.635 | 0.056 | 0.940 | 0.343 | 0.955 | 0.118 |
| fan | 0.145 | 0.318 | 0.458 | 0.567 | 0.510 | 0.478 |
| placebo_iso1 | 0.004 | 0.003 | 0.013 | -0.002 | 0.013 | -0.003 |
| placebo_cov1 | -0.332 | -0.121 | -0.713 | -0.387 | -0.763 | -0.082 |
| g9_raw | 0.031 | 0.105 | 0.123 | 0.174 | 0.139 | 0.997 |
| g9_clean | 0.019 | 0.095 | 0.101 | 0.175 | 0.116 | 1.000 |

Scale (the 6b unit is a 1-sd step along each vector's own natural projection):

| vector | sd along v | lambda for 1 sd (x mean norm) | max per-dim push of a 1-sd step (dim sds) | low-variance share | max per-dim push at lambda 0.1 (item 6) |
|---|---|---|---|---|---|
| probe_clean | 265.5 | 0.00479 | 67.05 | 0.895 | 1399 |
| pattern | 2460.0 | 0.04442 | 1.15 | 0.004 | 3 |
| pattern_raw | 2452.5 | 0.04428 | 1.12 | 0.003 | 3 |
| fan | 1268.1 | 0.02290 | 26.11 | 0.108 | 114 |
| placebo_iso1 | 48.2 | 0.00087 | 2.87 | 0.095 | 330 |
| placebo_cov1 | 1866.6 | 0.03370 | 2.15 | 0.012 | 6 |
| g9_raw | 344.8 | 0.00623 | 86.78 | 0.890 | 1394 |
| g9_clean | 265.5 | 0.00479 | 67.05 | 0.895 | 1399 |

STOP check for this site: {'n_homog_below': True, 'n_homog_above': True}

## lottery_L30 (secondary)

Cosines (columns: reference directions; nulls are 99th percentiles of |cos| with that reference):

| vector | n_homog_below | n_homog_above | n_slope_all | mod_grid_matched_clean | mod_frame_matched | probe_clean |
|---|---|---|---|---|---|---|
| *null iso / cov* | 0.036 / 0.506 | 0.036 / 0.440 | 0.035 / 0.894 | 0.036 / 0.627 | 0.036 / 0.891 | |
| probe_clean | 0.020 | 0.090 | 0.191 | 0.197 | 0.189 | 1.000 |
| pattern | 0.300 | -0.201 | 0.973 | 0.494 | 0.979 | 0.198 |
| pattern_raw | 0.287 | -0.070 | 0.742 | 0.460 | 0.710 | 0.185 |
| fan | 0.077 | 0.184 | 0.409 | 0.404 | 0.396 | 0.690 |
| placebo_iso1 | 0.011 | -0.011 | -0.004 | 0.017 | -0.004 | 0.010 |
| placebo_cov1 | -0.205 | -0.134 | -0.265 | -0.292 | -0.202 | -0.130 |

Scale (the 6b unit is a 1-sd step along each vector's own natural projection):

| vector | sd along v | lambda for 1 sd (x mean norm) | max per-dim push of a 1-sd step (dim sds) | low-variance share | max per-dim push at lambda 0.1 (item 6) |
|---|---|---|---|---|---|
| probe_clean | 45.9 | 0.00099 | 33.01 | 0.739 | 3342 |
| pattern | 273.9 | 0.00590 | 1.10 | 0.006 | 19 |
| pattern_raw | 259.5 | 0.00559 | 1.09 | 0.006 | 20 |
| fan | 110.0 | 0.00237 | 10.50 | 0.136 | 444 |
| placebo_iso1 | 4.6 | 0.00010 | 0.30 | 0.097 | 307 |
| placebo_cov1 | 308.0 | 0.00663 | 1.57 | 0.008 | 24 |

STOP check for this site: {'n_homog_below': True, 'n_homog_above': True}

## ultimatum_L40 (exploratory)

Cosines (columns: reference directions; nulls are 99th percentiles of |cos| with that reference):

| vector | n_homog_below | n_homog_above | n_slope_all | mod_grid_matched_clean | mod_frame_matched | probe_clean |
|---|---|---|---|---|---|---|
| *null iso / cov* | - | 0.037 / 0.980 | 0.037 / 0.977 | 0.035 / 0.618 | 0.035 / 0.952 | |
| probe_clean | - | -0.002 | 0.026 | 0.065 | 0.099 | 1.000 |
| pattern | - | 0.322 | 0.558 | 0.551 | 0.957 | 0.132 |
| pattern_raw | - | 0.347 | 0.580 | 0.546 | 0.964 | 0.130 |
| fan | - | 0.026 | 0.144 | 0.268 | 0.426 | 0.476 |
| placebo_iso1 | - | 0.017 | 0.013 | -0.003 | -0.003 | 0.027 |
| placebo_cov1 | - | -0.469 | -0.345 | 0.311 | 0.112 | 0.086 |

Scale (the 6b unit is a 1-sd step along each vector's own natural projection):

| vector | sd along v | lambda for 1 sd (x mean norm) | max per-dim push of a 1-sd step (dim sds) | low-variance share | max per-dim push at lambda 0.1 (item 6) |
|---|---|---|---|---|---|
| probe_clean | 182.5 | 0.00312 | 107.05 | 0.921 | 3435 |
| pattern | 1798.8 | 0.03072 | 1.22 | 0.005 | 4 |
| pattern_raw | 1832.0 | 0.03129 | 1.23 | 0.005 | 4 |
| fan | 758.9 | 0.01296 | 28.68 | 0.148 | 221 |
| placebo_iso1 | 50.4 | 0.00086 | 1.39 | 0.103 | 162 |
| placebo_cov1 | 1299.9 | 0.02220 | 1.86 | 0.013 | 8 |

STOP check for this site: {'n_homog_above': True}

## ultimatum_L46 (exploratory)

Cosines (columns: reference directions; nulls are 99th percentiles of |cos| with that reference):

| vector | n_homog_below | n_homog_above | n_slope_all | mod_grid_matched_clean | mod_frame_matched | probe_clean |
|---|---|---|---|---|---|---|
| *null iso / cov* | - | 0.034 / 0.972 | 0.034 / 0.973 | 0.033 / 0.680 | 0.037 / 0.953 | |
| probe_clean | - | 0.003 | 0.040 | 0.077 | 0.116 | 1.000 |
| pattern | - | 0.454 | 0.697 | 0.563 | 0.982 | 0.142 |
| pattern_raw | - | 0.401 | 0.654 | 0.572 | 0.971 | 0.147 |
| fan | - | 0.017 | 0.179 | 0.324 | 0.493 | 0.466 |
| placebo_iso1 | - | -0.007 | -0.013 | -0.006 | -0.019 | 0.004 |
| placebo_cov1 | - | -0.881 | -0.776 | 0.150 | -0.314 | 0.052 |

Scale (the 6b unit is a 1-sd step along each vector's own natural projection):

| vector | sd along v | lambda for 1 sd (x mean norm) | max per-dim push of a 1-sd step (dim sds) | low-variance share | max per-dim push at lambda 0.1 (item 6) |
|---|---|---|---|---|---|
| probe_clean | 288.2 | 0.00469 | 50.05 | 0.921 | 1068 |
| pattern | 2621.8 | 0.04263 | 1.23 | 0.004 | 3 |
| pattern_raw | 2539.8 | 0.04130 | 1.24 | 0.005 | 3 |
| fan | 1205.3 | 0.01960 | 22.73 | 0.112 | 116 |
| placebo_iso1 | 56.9 | 0.00092 | 1.00 | 0.097 | 108 |
| placebo_cov1 | 2588.7 | 0.04209 | 1.91 | 0.007 | 5 |

STOP check for this site: {'n_homog_above': True}

