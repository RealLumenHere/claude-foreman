# >>> wq3511 fleet PATH for non-interactive ssh — managed by foreman/ops/wq3511-noninteractive-path >>>
# Debian bash sources ~/.bashrc for non-interactive ssh commands (SSH_SOURCE_BASHRC
# patch; proven on this box, bash 5.3.9 — see the bundle README). This block must
# stay ABOVE the `case $- in *i*` interactive guard: everything below that guard
# never runs for Commander/fworker ssh calls, which is exactly the wq#3511 bug.
case ":$PATH:" in *":/home/lumen/bin:"*) ;; *) PATH="/home/lumen/bin:$PATH" ;; esac
export PATH
# <<< wq3511 <<<
