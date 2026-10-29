# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

$(eval $(call native-rust-app,tests/app/scheduler-bench,hyper-sched-bench,sched-bench,bin/sched-bench,development))
