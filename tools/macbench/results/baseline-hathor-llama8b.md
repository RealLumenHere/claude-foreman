### macbench results - hathor

| row | engine | quant | prefill 512 | prefill 4k | prefill 8k | 4k reuse | decode | 2x slowdown | RSS GB | pkg C max | fan max | W mean | agentic |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| baseline-hathor-llama8b | - | - | - | - | - | - | - | - | - | - | - | - | 0/8 |

prefill and decode are tok/s from server-side counters; 2x slowdown is pair wall time over solo wall time for a 2048-token prompt (about 2 means prefill is time-sliced, about 1 means parallel).

#### agentic tasks - baseline-hathor-llama8b

| task | kind | pass | turns | tool calls | tool errors | wall s | diff lines |
|---|---|---|---|---|---|---|---|
| e1_rename | mechanical-edit | no | 10 | 9 | 4 | 97 | 0 |
| e2_default | mechanical-edit | no | 2 | 1 | 1 | 46 | 0 |
| e3_fstrings | mechanical-edit | no | 1 | 0 | 0 | 18 | 0 |
| e4_thread_param | mechanical-edit | no | 1 | 0 | 0 | 238 | 0 |
| p1_disk | probe-build | no | 1 | 0 | 0 | 305 | 0 |
| p2_ports | probe-build | no | 2 | 1 | 1 | 107 | 0 |
| p3_logs | probe-build | no | 1 | 0 | 0 | 224 | 0 |
| p4_env | probe-build | no | 3 | 2 | 2 | 118 | 0 |
