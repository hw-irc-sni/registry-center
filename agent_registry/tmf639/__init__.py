# Copyright (c) 2026 Huawei Technologies Co., Ltd.
# All Rights Reserved.
#
# SPDX-License-Identifier: Apache-2.0

"""TMF639 Resource Inventory Management v5 facade (see router.py)."""

from agent_registry.tmf639.router import TMFError, tmf639_router, tmf_error_handler

__all__ = ['TMFError', 'tmf639_router', 'tmf_error_handler']
