"""Pexpect drive script for an Alpine ISO install over the serial console.

Modeled on the Alpine wiki's Packer installation pattern:
https://wiki.alpinelinux.org/wiki/Packer_installation. Anchors on stable
substrings (`login:`, `# `, password prompts) rather than full lines, since
exact line text varies between minor releases.

Caller drives this synchronously in a thread (pexpect is sync); coordination
is `loop.run_in_executor`.
"""

# pexpect ships no type stubs.
# pyright: reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportMissingTypeArgument=false

from __future__ import annotations

from typing import Protocol


class _Spawn(Protocol):
    """The narrow slice of pexpect.SocketSpawn we depend on."""

    def expect(self, pattern: object, timeout: float = ...) -> int: ...
    def sendline(self, line: str) -> None: ...


# Disposable: setup-alpine demands a value, but key-based SSH is the real auth path.
_DEFAULT_ROOT_PASSWORD = "uqmm-disposable"

# Strings that mean "the install crashed; don't keep waiting for the prompt."
# Per docs/research/alpine-unattended.md, alternate every expect with these so a
# panic fails loudly instead of hanging until the per-step timeout.
_PANIC_PATTERNS = ["Kernel panic", "Call Trace:", "Oops:", "Aieee"]


def _expect_or_panic(spawn: _Spawn, pattern: str, timeout: float) -> None:
    """Expect `pattern`; raise PanicDetected if a kernel panic shows up first."""
    idx = spawn.expect([pattern, *_PANIC_PATTERNS], timeout=timeout)
    if idx > 0:
        raise PanicDetected(_PANIC_PATTERNS[idx - 1])


class PanicDetected(RuntimeError):
    """Kernel panic / oops observed on the install console."""


def drive_install(
    spawn: _Spawn,
    answers_url: str,
    root_password: str | None = None,
) -> None:
    """Run setup-alpine -ef <answers> and trigger reboot.

    Steps:
    1. Wait for `localhost login:`, log in as root (no password on live ISO).
    2. Widen the terminal so wrapped output doesn't break our regex anchors.
    3. Bring up eth0 + DHCP so wget can reach the host's answer-file server.
    4. wget the answer file and invoke setup-alpine -ef.
    5. Type the root password twice (setup-alpine asks even with -f).
    6. Wait for the post-install shell prompt; reboot.

    QEMU's `-no-reboot` makes the reboot trigger process exit; the caller
    sees that via QMP SHUTDOWN or process.wait().
    """
    pw = root_password or _DEFAULT_ROOT_PASSWORD

    # 1. live-ISO login (root, no password)
    _expect_or_panic(spawn, "login: ", timeout=180)
    spawn.sendline("root")
    _expect_or_panic(spawn, "# ", timeout=15)

    # 2. widen the terminal so our regex anchors don't trip on line wraps
    spawn.sendline("stty cols 200")
    _expect_or_panic(spawn, "# ", timeout=10)

    # 3. DHCP — alpine-virt brings eth0 down by default
    spawn.sendline("ifconfig eth0 up && udhcpc -i eth0")
    _expect_or_panic(spawn, "# ", timeout=30)

    # 4. fetch answers + run installer (ERASE_DISKS suppresses the disk-erase prompt)
    spawn.sendline(
        f"wget -O /tmp/answers {answers_url} && "
        "export ERASE_DISKS=/dev/vda && "
        "setup-alpine -ef /tmp/answers"
    )
    # 5. setup-alpine asks for new root password twice, even with -f.
    _expect_or_panic(spawn, "New password: ", timeout=300)
    spawn.sendline(pw)
    _expect_or_panic(spawn, "Retype password: ", timeout=10)
    spawn.sendline(pw)

    # 6. wait for installer completion; the shell prompt returns when done.
    _expect_or_panic(spawn, "# ", timeout=900)
    spawn.sendline("reboot")
