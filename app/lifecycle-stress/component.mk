# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

$(eval $(call native-rust-app,tests/app/lifecycle-stress,hyper-lifecycle-stress,lifecycle-stress,bin/lifecycle-stress,development))
