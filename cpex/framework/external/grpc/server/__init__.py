# -*- coding: utf-8 -*-
"""Location: ./cpex/framework/external/grpc/server/__init__.py
Copyright 2025
SPDX-License-Identifier: Apache-2.0
Authors: Teryl Taylor

gRPC server package for external plugin transport.

This package provides the gRPC servicer implementations that wrap the
ExternalPluginServer to expose plugin functionality via gRPC.
"""

from cpex.framework.external.grpc.server.server import GrpcHealthServicer, GrpcPluginServicer

__all__ = ["GrpcPluginServicer", "GrpcHealthServicer"]
