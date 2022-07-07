import subprocess as sp
from pathlib import Path
import shutil
import click
from git import Repo
from ruamel.yaml import YAML
import tabulate
import glob
from .import_pkgs import write_awesome_pages

yaml = YAML(typ="rt")
# indent 2 spaces extra on lists
yaml.indent(mapping=2, sequence=4, offset=2)
# prevent opinionated line wrapping
yaml.width = 1000


@click.group(
    context_settings={"help_option_names": ["-h", "--help"]},
    epilog="Built and maintained by @razzle",
)
def cli():
    pass


class SubmoduleRepo:
    def __init__(self, name):
        self.name = name
        self.path = Path.cwd() / "submodules" / name
        self.repo = Repo(self.path)

    def pull(self):
        self.repo.git.pull()

    def checkout(self, ref):
        if self.repo.is_dirty():
            print(f"{self.name} repo has pending changes, please commit or stash them")
            return
        self.repo.git.checkout(ref)
        # print(f"{self.name} checked out @{ref}")


class BigBangRepo(SubmoduleRepo):
    def __init__(self):
        SubmoduleRepo.__init__(self, "bigbang")

    def get_pkgs(self):
        pkgs = {}
        values_path = self.path / "chart" / "values.yaml"
        with open(values_path) as values_yaml:
            values = yaml.load(values_yaml)

        # core
        for _, v in values.items():
            if isinstance(v, dict) and "git" in v:
                pkg = v["git"]
                pkg["name"] = pkg["repo"].split("/")[-1].split(".")[0]
                pkg["title"] = pkg["name"].replace("-", " ").title()
                pkg.pop("path", None)
                pkg["type"] = "Core"
                pkgs[pkg["name"]] = pkg
        # addons
        for _, v in values["addons"].items():
            if isinstance(v, dict) and "git" in v:
                pkg = v["git"]
                pkg["name"] = pkg["repo"].split("/")[-1].split(".")[0]
                pkg["title"] = pkg["name"].replace("-", " ").title()
                pkg.pop("path", None)
                pkg["type"] = "Addon"
                pkgs[pkg["name"]] = pkg

        return pkgs

    def get_tags(self):
        versions = []
        for tag in reversed(
            sorted(self.repo.tags, key=lambda t: t.commit.committed_datetime)
        ):
            if "rc" in tag.name:
                # skip rc versions
                continue
            elif tag.name == "":
                # skip blank version(s)
                continue

            versions.append(tag.name)

        return versions


@click.command(help="List all bb pkgs")
def pkgs():
    bb = BigBangRepo()
    pkgs = bb.get_pkgs()
    table = []
    headers = ["Name", "Added?", "Type", "Repo", "Tag"]
    for _, v in pkgs.items():
        is_submodule = Path.cwd().joinpath("submodules").joinpath(v["name"]).exists()
        table.append([v["name"], is_submodule, v["type"], v["repo"][49:], v["tag"]])

    print(tabulate.tabulate(table, headers=headers))
    return table


@click.command()
def tags():
    bb = BigBangRepo()
    tags = bb.get_tags()
    import json

    print(json.dumps(tags, indent=2))
    return tags


def setup():
    shutil.rmtree("docs", ignore_errors=True, onerror=None)
    shutil.copytree(
        "base", "docs", ignore=shutil.ignore_patterns("*.yaml"), dirs_exist_ok=True
    )


def cleanup():
    shutil.rmtree("docs", ignore_errors=True, onerror=None)


@click.command()
@click.option("-l", "--last-x-tags", default=1, type=click.IntRange(1, 9, clamp=True))
@click.option("-c", "--clean", is_flag=True)
@click.option("-d", "--dev", is_flag=True)
def compile(last_x_tags, clean, dev):
    bb = BigBangRepo()
    tags = bb.get_tags()
    tags_to_compile = tags[:last_x_tags]
    tags_to_compile.reverse()

    setup()
    bb.checkout("master")

    if last_x_tags == 1:
        bb.checkout(tags_to_compile[0])
        pkgs = bb.get_pkgs()

        configs = glob.iglob("base/**/config.yaml", recursive=True)

        for fpath in configs:
            dst_root = Path(fpath.replace("base/", "docs/").replace("/config.yaml", ""))

            with open(fpath, "r") as f:
                config = yaml.load(f)

            src_root = Path().cwd().joinpath(config["source"]) or None

            if src_root is None:
                print(f"{fpath} config is missing a `source` key")
                continue

            repo = SubmoduleRepo(str(src_root).split("/")[-1])

            if repo.name != "bigbang":
                repo.checkout(pkgs[repo.name]["tag"])

            if repo.name == "bigbang":
                shutil.copy2(
                    src_root / "Packages.md",
                    Path().cwd().joinpath("docs/packages/index.md"),
                )
                with open(Path().cwd().joinpath("docs/.pages"), "r") as f:
                    dot_pages = yaml.load(f)

                dot_pages["nav"][3]["📋 Release Notes"] += "/" + tags_to_compile[0]

                with open(Path().cwd().joinpath("docs/.pages"), "w") as f:
                    yaml.dump(dot_pages, f)

            shutil.copytree(
                src_root,
                dst_root,
                ignore=shutil.ignore_patterns(*config["ignore_patterns"]),
                dirs_exist_ok=True,
            )

            write_awesome_pages(config, dst_root / ".pages")

        # patch docs/docs references
        pkg_docs_glob = glob.iglob("docs/packages/**/docs/*.md", recursive=True)
        for doc in pkg_docs_glob:
            with open(doc, "r") as f:
                content = f.read()

            import re

            without_bad_links = re.sub(r"\]\(\.\/docs", "](", content)
            without_bad_links_ex = re.sub(r"\]\(docs", "](", without_bad_links)

            with open(doc, "w") as f:
                f.write(without_bad_links_ex)
                f.close()
        # end patch

        if dev:
            sp.run(["mkdocs", "serve"])
        else:
            sp.run(["mkdocs", "build", "--clean"])

    else:
        for tag in tags_to_compile:
            bb.checkout(tags_to_compile[tag])
            pkgs = bb.get_pkgs()
            for k, v in pkgs.items():
                base_exists = Path.cwd().joinpath("submodules").joinpath(k).exists()
                if base_exists == False:
                    print(f"Base template does not exist in base/packages/{k}")
                    print(
                        "You will have to `mkdir`, and then `touch config.yaml` for that pkg"
                    )

    if clean:
        cleanup()
    bb.checkout("master")


cli.add_command(pkgs)
cli.add_command(tags)
cli.add_command(compile)
