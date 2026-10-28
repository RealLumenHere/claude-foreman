# limpet-plugin-flashrom

First-party SPI flash programmer plugin for Limpet. No third-party flashrom-mcp
exists — this fills the gap with a thin MCP wrapper over the flashrom CLI.

## Tools

| Tool | Energize | Description |
|---|---|---|
| `flashrom_probe` | No | Detect flash chips on a programmer |
| `flashrom_read` | No | Read flash contents to a file |
| `flashrom_write` | **Yes** | Write an image to flash |
| `flashrom_erase` | **Yes** | Erase the flash chip |
| `flashrom_verify` | No | Verify flash matches an image |

## Supported programmers

| Programmer | VID:PID | flashrom -p argument |
|---|---|---|
| CH341A | 1a86:5512 | `ch341a_spi` |
| XGecu TL866II+ | 04d8:e11c | `minipro` |
| Pico serprog | 2e8a:000a | `serprog:dev=/dev/ttyACM0:115200` |
| Internal chipset | — | `internal` |

## Safety

The agent workflow for flash operations:
1. `flashrom_probe` — identify the chip
2. `flashrom_read` — dump current contents (backup!)
3. `flashrom_read` again — second dump, compare checksums
4. Only then: `flashrom_write` (energize-gated)
5. `flashrom_verify` — confirm the write

`flashrom_write`/`flashrom_erase` require a `confirm_token` minted by an operator
(`python3 harness/energize_confirm.py issue flashrom.write_flash` /
`flashrom.erase_chip`, run from an interactive terminal — it refuses otherwise, so
a headless/agent session can't mint its own). Before wq#4747 these tools said
"REQUIRES energize-confirm gate" in their docstrings but enforced nothing; the gate
is real now, and a model-supplied bool does not satisfy it (hal-spec.md §9.2a).

## License

GPL-2.0-only (flashrom upstream)
