from uqmm.builders.alpine import render_answers
from uqmm.config import VMConfig


def make_cfg(**kw: object) -> VMConfig:
    base: dict[str, object] = {
        "name": "al321",
        "os": "alpine",
        "version": "3.21",
        "ssh_authorized_keys": ["ssh-ed25519 AAA test@host"],
    }
    base.update(kw)
    return VMConfig(**base)  # pyright: ignore[reportArgumentType]


def test_answers_contains_required_keys() -> None:
    rendered = render_answers(make_cfg())
    for key in (
        "KEYMAPOPTS",
        "HOSTNAMEOPTS",
        "INTERFACESOPTS",
        "DNSOPTS",
        "TIMEZONEOPTS",
        "PROXYOPTS",
        "APKREPOSOPTS",
        "USEROPTS",
        "USERSSHKEY",
        "SSHDOPTS",
        "NTPOPTS",
        "DISKOPTS",
        "LBUOPTS",
        "APKCACHEOPTS",
    ):
        assert key + "=" in rendered, f"{key} missing from answers file"


def test_answers_uses_effective_hostname() -> None:
    rendered = render_answers(make_cfg(name="al321", hostname="alpha"))
    assert 'HOSTNAMEOPTS="-n alpha"' in rendered


def test_answers_hostname_falls_back_to_name() -> None:
    rendered = render_answers(make_cfg(name="al321", hostname=None))
    assert 'HOSTNAMEOPTS="-n al321"' in rendered


def test_answers_includes_user_with_keys() -> None:
    rendered = render_answers(
        make_cfg(user="alice", ssh_authorized_keys=["ssh-rsa AAA k1", "ssh-ed25519 BBB k2"])
    )
    assert "USEROPTS=" in rendered
    assert "alice" in rendered
    # USERSSHKEY accepts a single key value; multiple keys must be joined with
    # newlines so setup-alpine can write them all into authorized_keys.
    assert "ssh-rsa AAA k1" in rendered
    assert "ssh-ed25519 BBB k2" in rendered


def test_answers_uses_sys_install_to_vda() -> None:
    rendered = render_answers(make_cfg())
    # `-m sys -s 0 /dev/vda` per phase-3 doc and Alpine wiki.
    assert "DISKOPTS=" in rendered
    assert "/dev/vda" in rendered
    assert "-m sys" in rendered


def test_answers_uses_dhcp() -> None:
    rendered = render_answers(make_cfg())
    assert "auto eth0" in rendered
    assert "iface eth0 inet dhcp" in rendered


def test_answers_uses_openssh_not_dropbear() -> None:
    rendered = render_answers(make_cfg())
    assert "SSHDOPTS=" in rendered
    assert "openssh" in rendered


def test_answers_apkrepos_includes_version_path() -> None:
    # setup-apkrepos in 3.21 writes its positional URL arg verbatim — it does
    # NOT auto-append /v$VER/main as the wiki suggests. Without an explicit
    # version path, apk fetches end up at /alpine/x86_64/APKINDEX.tar.gz
    # (no version) and `setup-disk -m sys` errors on syslinux.
    rendered = render_answers(make_cfg(version="3.21"))
    apkrepos_line = next(ln for ln in rendered.splitlines() if ln.startswith("APKREPOSOPTS="))
    assert "/v3.21/main" in apkrepos_line


def test_answers_dnsopts_names_a_resolver() -> None:
    # An empty DNSOPTS makes setup-dns clobber /etc/resolv.conf. Pin
    # SLiRP's built-in resolver.
    rendered = render_answers(make_cfg())
    assert 'DNSOPTS=""' not in rendered
    assert "10.0.2.3" in rendered


def test_answers_useropts_has_no_embedded_quotes() -> None:
    # The answers file is sourced by sh; embedded single quotes are kept
    # literally on word-split, so `-g 'wheel,…'` would pass the group
    # `'wheel` to addgroup. The list has no whitespace — leave it bare.
    rendered = render_answers(make_cfg(user="alice"))
    useropts_line = next(ln for ln in rendered.splitlines() if ln.startswith("USEROPTS="))
    assert "'" not in useropts_line
