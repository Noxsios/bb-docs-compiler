import glob
import os
import shutil
import subprocess as sp
from copy import deepcopy
from pathlib import Path

import click
import semver
from deepmerge import always_merger as merge
from git import GitCommandError
from rich import print
from ruamel.yaml import YAML

from .prenpost import cleanup, postflight, preflight
from .repo import BigBangRepo, SubmoduleRepo, pull_latest
from .utils import (
    add_frontmatter,
    get_release_notes,
    parse_values_table_from_helm_docs,
    patch_values_table_from_helm_docs,
    write_awesome_pages,
    write_values_md,
)

yaml = YAML(typ="rt")
# indent 2 spaces extra on lists
yaml.indent(mapping=2, sequence=4, offset=2)
# prevent opinionated line wrapping
yaml.width = 1000


def compile(bb, tag):
    pkgs = bb.get_pkgs()
    docs_root = Path().cwd() / "docs"

    with Path().cwd().joinpath("docs-compiler.yaml").open("r") as f:
        meta = yaml.load(f)

    ## bigbang section
    bb_config = meta["/"]
    notes = get_release_notes(tag)
    if notes != None:
        bb_config["pages"]["nav"][4]["📋 Release Notes"] = "release-notes.md"
        with open("docs/release-notes.md", "w") as f:
            f.write(notes)
            f.close()
    bb.copy_files(
        Path().cwd() / "submodules" / "bigbang", docs_root, bb_config["include"]
    )
    write_awesome_pages(bb_config["pages"], docs_root / ".pages")

    bb_values_table = parse_values_table_from_helm_docs(
        "submodules/bigbang/docs/understanding-bigbang/configuration/base-config.md",
        r"## Values(.*)",
    )

    write_values_md("docs/values.md", bb_values_table, "Big Bang")

    root_level_md = glob.iglob("docs/*.md")
    for md in root_level_md:
        if md == "docs/about.md" or md == "docs/values.md":
            add_frontmatter(
                md,
                {
                    "hide": ["navigation"],
                },
            )
        else:
            add_frontmatter(
                md,
                {
                    "hide": ["navigation"],
                    "revision_date": bb.get_revision_date(md.replace("docs/", "", 1)),
                },
            )

    bb.patch_external_refs("**/*.md", docs_root)

    pkgs_configs = meta["packages"]
    template_config = meta["packages"]["_template"]
    del meta["packages"]["_template"]
    for pkg in pkgs_configs:
        tmpl = deepcopy(template_config)
        pkg_config = merge.merge(tmpl, meta["packages"][pkg])
        pkg_name = pkg_config["source"].split("/")[-1]
        if pkg_name not in pkgs.keys():
            # this means that we are trying to build a version of the docs that does not have this (newer) pkg
            # skip it
            continue
        repo = SubmoduleRepo(pkg_name)
        repo.checkout(pkgs[pkg]["tag"])
        dst_root = docs_root / "packages" / pkg
        os.makedirs(dst_root)
        src_root = Path().cwd().joinpath(pkg_config["source"])
        repo.copy_files(src_root, dst_root, pkg_config["include"])
        write_awesome_pages(pkg_config["pages"], dst_root / ".pages")
        repo.patch_external_refs("**/*.md", dst_root)

    shutil.copy2(
        "submodules/bigbang/docs/packages.md",
        "docs/packages/index.md",
    )

    pkg_readmes = glob.iglob("docs/packages/*/README.md")
    for md in pkg_readmes:
        pkg_name = md.split("/")[2]
        values_table = parse_values_table_from_helm_docs(
            md.replace("docs/packages/", "submodules/", 1),
            r"## Values(.*?)## Contributing",
        )
        patch_values_table_from_helm_docs(
            f"docs/packages/{pkg_name}/README.md", values_table
        )
        write_values_md(f"docs/packages/{pkg_name}/values.md", values_table, pkg_name)
        pkg_tag = pkgs[pkg_name]["tag"]
        add_frontmatter(
            f"docs/packages/{pkg_name}/values.md",
            {"tags": ["values", pkg_name, pkg_tag]},
        )

    bb_docs = glob.iglob("docs/docs/**/*.md", recursive=True)
    for md in bb_docs:
        add_frontmatter(
            md,
            {
                "tags": ["bigbang", tag],
                "revision_date": bb.get_revision_date(
                    md.replace("docs/docs/", "./docs/", 1)
                ),
            },
        )

    pkg_docs = glob.iglob("docs/packages/**/*.md", recursive=True)
    for md in pkg_docs:
        pkg_name = md.split("/")[2]
        if (
            md == "docs/packages/index.md"
            or md == f"docs/packages/{pkg_name}/values.md"
        ):
            continue
        pkg_tag = pkgs[pkg_name]["tag"]
        add_frontmatter(
            md,
            {
                "tags": ["package", pkg_name, pkg_tag],
                "revision_date": SubmoduleRepo(pkg_name).get_revision_date(
                    md.replace(f"docs/packages/{pkg_name}/", "./", 1)
                ),
            },
        )

    # patch packages nav
    with open("docs/packages/.pages", "w") as f:
        dot_pages = {}
        dot_pages["nav"] = [{"Home": "index.md"}]
        sorted_pkgs = sorted(meta["packages"])
        for pkg in sorted_pkgs:
            dot_pages["nav"].append({pkg: pkg})
        yaml.dump(dot_pages, f)
        f.close()
    # end patch


@click.command(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog="Built and maintained by @razzle",
)
@click.option(
    "-t",
    "--tag",
    help="Build docs from Big Bang tag <tag>",
    default="latest",
    type=click.STRING,
)
@click.option(
    "-b", "--branch", help="Build docs from Big Bang branch <branch>", type=click.STRING
)
@click.option(
    "--pre-release",
    help="Build for `release-1.X.0` (only for release engineering)",
    is_flag=True,
)
@click.option(
    "-c", "--clean", help="Destroy + reset resources after build", is_flag=True
)
@click.option(
    "-o",
    "--outdir",
    help="Output build folder, default (site)",
    default="site",
    type=click.STRING,
)
@click.option(
    "--no-build",
    help="Compile the `docs` folder but do not render w/ mkdocs",
    is_flag=True,
)
@click.option("-d", "--dev", help="Run `mkdocs serve` after build", is_flag=True)
def compiler(tag, branch, pre_release, clean, outdir, no_build, dev):
    ref = None
    if (
        tag != "latest"
        and branch
        or tag != "latest"
        and pre_release
        or branch
        and pre_release
    ):
        print(
            f"[red]ERROR[/red]    - Please use either '--branch' or '--tag' or '--pre-release', not a combination"
        )
        exit(1)
    pull_latest()
    bb = BigBangRepo()
    tags = bb.get_tags()

    if tag == "latest":
        ref = tags[0]
    if tag != "latest":
        ref = tag
        try:
            ver = semver.VersionInfo.parse(ref)
            if ver.major == 1 and ver.minor <= 40:
                print(
                    "[red]ERROR[/red]    - Only versions 1.40.0+ are supported via this docs generator"
                )
                exit(1)
        except ValueError:
            print(
                f"[red]ERROR[/red]    - Tag '{ref}' provided is not a valid semver string"
            )
            exit(1)
    elif branch:
        ref = branch
    elif pre_release:
        next_release_tag_x = "release-1." + str(int(tags[0].split(".")[1]) + 1) + ".x"
        ref = next_release_tag_x
        try:
            bb.checkout(next_release_tag_x)
        except GitCommandError as e:
            if "did not match any file(s) known to git" in e.stderr:
                print(
                    f"[red]ERROR[/red]    - Failed to checkout ({next_release_tag_x}) on bigbang, verify you have correctly run R2-D2"
                )
                exit(1)

    ### TEMP MANUAL OVERRIDE TO USE `1272-draft-follow-on-follow-on-docs-design-update` branch
    ref = "1272-draft-follow-on-follow-on-docs-design-update"

    try:
        bb.checkout(ref)
    except GitCommandError as e:
        if "did not match any file(s) known to git" in e.stderr:
            print(
                f"[red]ERROR[/red]    - Failed to checkout ({ref}) on bigbang, verify branch/tag exists in Repo1"
            )
            exit(1)

    print(f"INFO     -  Compiling docs for Big Bang version '{ref}'")
    preflight(bb)
    compile(bb, ref)
    postflight()

    if dev and no_build == False:
        sp.run(["mkdocs", "serve"])
    elif no_build:
        print("INFO     -  Documentation compiled to `./docs`")
    else:
        sp.run(["mkdocs", "build", "--clean", "--site-dir", outdir])

    if clean:
        cleanup()
        sp.run(
            ["git", "submodule", "update", "--init", "--recursive"], capture_output=True
        )
