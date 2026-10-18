# SPDX-FileCopyrightText: 2026 roolrz
# SPDX-License-Identifier: Apache-2.0

$(eval $(call native-rust-app,tests/app/retirement-probe,hyper-retirement-probe,retirement-probe,bin/retirement-probe,development))
