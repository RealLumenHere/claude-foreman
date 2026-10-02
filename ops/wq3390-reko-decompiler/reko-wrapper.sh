#!/usr/bin/env bash
# wq#3390 — thin launcher so `reko` on PATH runs the .NET-hosted CmdLine driver
# installed by ./install.sh under /home/lumen/opt.
exec /home/lumen/opt/dotnet/dotnet /home/lumen/opt/reko/decompile.dll "$@"
