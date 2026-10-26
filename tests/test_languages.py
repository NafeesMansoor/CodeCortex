"""Per-language extraction tests for the table-driven parsers.

Each case pins the definitions, calls, imports and base types one small
idiomatic file must produce, so a grammar upgrade that renames a node type
fails here instead of silently emptying that language's part of the graph.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from codecortex.core.parsers import grammars
from codecortex.core.parsers.registry import PARSER_CLASSES, build_extension_map, get_parser
from codecortex.core.types import EdgeKind
from codecortex.pipeline.context_builder import CodeCortexPipeline, PipelineConfig


def _parse(filename: str, source: str):
    parser = build_extension_map()[Path(filename).suffix.lstrip(".")]
    result = parser.parse(source, filename)
    assert result.errors == []
    return result


def _nodes(result) -> set[tuple[str, str]]:
    return {(n.kind.value, n.qualified_name) for n in result.nodes}


def _edges(result, kind: EdgeKind) -> set[tuple[str, str]]:
    return {(e.source_qualified, e.target_qualified) for e in result.edges if e.kind == kind}


def _imports(result) -> set[str]:
    return {target for _, target in _edges(result, EdgeKind.IMPORTS_FROM)}


CASES = {
    "main_test.go": (
        """package main
import (
    "fmt"
    api "example.com/api"
)
type Server struct{}
type Handler interface{ Serve() }
func (s *Server) Serve() { fmt.Println("x"); helper() }
func helper() {}
func TestServe(t *testing.T) {}
""",
        {
            ("Struct", "Server"),
            ("Interface", "Handler"),
            ("Method", "Server.Serve"),
            ("Function", "helper"),
            ("Test", "TestServe"),
        },
        {("Server.Serve", "Println"), ("Server.Serve", "helper")},
        {"fmt", "example.com/api"},
        set(),
    ),
    "lib.rs": (
        """use std::collections::{HashMap, HashSet};
struct Point {}
enum Shape { Circle }
trait Area { fn area(&self) -> f64; }
impl Area for Point { fn area(&self) -> f64 { helper(); self.norm(); Point::new() } }
fn helper() {}
#[test]
fn computes_area() {}
""",
        {
            ("Struct", "Point"),
            ("Enum", "Shape"),
            ("Interface", "Area"),
            ("Method", "Point.area"),
            ("Function", "helper"),
            ("Test", "computes_area"),
        },
        {("Point.area", "helper"), ("Point.area", "norm"), ("Point.area", "new")},
        {"std::collections"},
        {(EdgeKind.IMPLEMENTS, "Point", "Area")},
    ),
    "Service.java": (
        """import java.util.List;
public class Service extends Base implements Runnable {
    public Service() {}
    public void run() { helper(); this.log(); new Worker(); }
    @Test void checksRun() {}
}
interface Runnable {}
enum Mode { ON }
""",
        {
            ("Class", "Service"),
            ("Method", "Service.Service"),
            ("Method", "Service.run"),
            ("Test", "Service.checksRun"),
            ("Interface", "Runnable"),
            ("Enum", "Mode"),
        },
        {("Service.run", "helper"), ("Service.run", "log"), ("Service.run", "Worker")},
        {"java.util.List"},
        {(EdgeKind.INHERITS, "Service", "Base"), (EdgeKind.IMPLEMENTS, "Service", "Runnable")},
    ),
    "Service.cs": (
        """using System.Text;
namespace App {
    public class Service : Base, IRunnable {
        public void Run() { Helper(); logger.Write(); }
        [Fact] public void RunsCleanly() {}
    }
    interface IRunnable {}
    struct Pair {}
}
""",
        {
            ("Class", "Service"),
            ("Method", "Service.Run"),
            ("Test", "Service.RunsCleanly"),
            ("Interface", "IRunnable"),
            ("Struct", "Pair"),
        },
        {("Service.Run", "Helper"), ("Service.Run", "Write")},
        {"System.Text"},
        {(EdgeKind.INHERITS, "Service", "Base"), (EdgeKind.IMPLEMENTS, "Service", "IRunnable")},
    ),
    "util.c": (
        """#include <stdio.h>
#include "util.h"
struct point { int x; };
static int *scale(int a) { printf("x"); return helper(a); }
""",
        {("Struct", "point"), ("Function", "scale")},
        {("scale", "printf"), ("scale", "helper")},
        {"stdio.h", "util.h"},
        set(),
    ),
    "widget.cpp": (
        """#include <vector>
class Widget : public Base { void draw(); };
void Widget::draw() { paint(); canvas.flush(); std::sort(); }
""",
        {("Class", "Widget"), ("Method", "Widget.draw")},
        {("Widget.draw", "paint"), ("Widget.draw", "flush"), ("Widget.draw", "sort")},
        {"vector"},
        {(EdgeKind.INHERITS, "Widget", "Base")},
    ),
    "client.rb": (
        """require "json"
require_relative "support/http"
module Api
  class Client < Base
    def fetch(path); request(path); http.get(path); end
    def self.build; end
  end
end
""",
        {
            ("Module", "Api"),
            ("Class", "Api.Client"),
            ("Method", "Api.Client.fetch"),
            ("Method", "Api.Client.build"),
        },
        {("Api.Client.fetch", "request"), ("Api.Client.fetch", "get")},
        {"json", "support/http"},
        {(EdgeKind.INHERITS, "Api.Client", "Base")},
    ),
    "Client.kt": (
        """import kotlinx.coroutines.delay
class Client : Base(), Closeable { fun fetch() { request(); http.get() } }
interface Closeable {}
fun build() {}
""",
        {
            ("Class", "Client"),
            ("Method", "Client.fetch"),
            ("Interface", "Closeable"),
            ("Function", "build"),
        },
        {("Client.fetch", "request"), ("Client.fetch", "get")},
        {"kotlinx.coroutines.delay"},
        {(EdgeKind.INHERITS, "Client", "Base"), (EdgeKind.IMPLEMENTS, "Client", "Closeable")},
    ),
    "Client.swift": (
        """import Foundation
class Client: Base { func fetch() { request(); http.get() } }
struct Config {}
protocol Closeable {}
enum Mode { case on }
extension Client { func close() {} }
""",
        {
            ("Class", "Client"),
            ("Method", "Client.fetch"),
            ("Method", "Client.close"),
            ("Struct", "Config"),
            ("Interface", "Closeable"),
            ("Enum", "Mode"),
        },
        {("Client.fetch", "request"), ("Client.fetch", "get")},
        {"Foundation"},
        {(EdgeKind.INHERITS, "Client", "Base")},
    ),
    "Client.scala": (
        """import scala.util.Try
class Client extends Base { def fetch(): Unit = { request(); http.get() } }
trait Closeable
object Client { def apply() = new Client() }
""",
        {
            ("Class", "Client"),
            ("Method", "Client.fetch"),
            ("Interface", "Closeable"),
            ("Method", "Client.apply"),
        },
        {("Client.fetch", "request"), ("Client.fetch", "get")},
        {"scala.util.Try"},
        {(EdgeKind.INHERITS, "Client", "Base")},
    ),
    "client.lua": (
        """local json = require("json")
local M = {}
function M.fetch(path) request(path); http:get(path) end
local function helper() end
""",
        {("Method", "M.fetch"), ("Function", "helper")},
        {("M.fetch", "request"), ("M.fetch", "get")},
        {"json"},
        set(),
    ),
    "deploy.sh": (
        """source ./lib.sh
build() { compile; echo done; }
function deploy { build; }
""",
        {("Function", "build"), ("Function", "deploy")},
        {("build", "compile"), ("build", "echo"), ("deploy", "build")},
        {"./lib.sh"},
        set(),
    ),
    "service.mjs": (
        """import { db } from "./db.js";
const fs = require("fs");
export const handler = async (req) => { validate(req); return db.save(req); };
class UserService extends BaseService {
  login(u) { return this.auth.check(u); }
}
function main() { handler(1); new UserService(); [1].map((x) => helper(x)); }
const lazy = () => import("./lazy.js");
""",
        {
            ("Function", "handler"),
            ("Class", "UserService"),
            ("Method", "UserService.login"),
            ("Function", "main"),
            ("Function", "lazy"),
        },
        {
            ("handler", "validate"),
            ("handler", "save"),
            ("UserService.login", "check"),
            ("main", "handler"),
            ("main", "UserService"),
            ("main", "helper"),
        },
        {"./db.js", "fs", "./lazy.js"},
        {(EdgeKind.INHERITS, "UserService", "BaseService")},
    ),
    "UserController.php": (
        """<?php
namespace App\\Http;
use App\\Models\\User;
use Illuminate\\Support\\{Str, Arr as A};
require_once __DIR__ . "/helpers.php";
trait Audits {}
class UserController extends Controller implements Responder {
    use Audits;
    public function show($id) { abort_if(!$id); $this->authorize(); User::find($id); new Resource(); }
    public function testShow() {}
}
""",
        {
            ("Class", "Audits"),
            ("Class", "UserController"),
            ("Method", "UserController.show"),
            ("Test", "UserController.testShow"),
        },
        {
            ("UserController.show", "abort_if"),
            ("UserController.show", "authorize"),
            ("UserController.show", "find"),
            ("UserController.show", "Resource"),
        },
        {
            "App\\Models\\User",
            "Illuminate\\Support\\Str",
            "Illuminate\\Support\\Arr",
            "/helpers.php",
        },
        {
            (EdgeKind.INHERITS, "UserController", "Controller"),
            (EdgeKind.IMPLEMENTS, "UserController", "Responder"),
            (EdgeKind.EXTENDS, "UserController", "Audits"),
        },
    ),
    "repo.ts": (
        """import { Base } from "./base";
export * from "./types";
export abstract class Repo<T> extends Base<T> implements Store, Disposable {
  find(id: string): T { return query(id); }
}
interface Store extends Named {}
enum Color { Red }
type Id = string;
""",
        {
            ("Class", "Repo"),
            ("Method", "Repo.find"),
            ("Interface", "Store"),
            ("Enum", "Color"),
            ("Type", "Id"),
        },
        {("Repo.find", "query")},
        {"./base", "./types"},
        {
            (EdgeKind.INHERITS, "Repo", "Base"),
            (EdgeKind.IMPLEMENTS, "Repo", "Store"),
            (EdgeKind.IMPLEMENTS, "Repo", "Disposable"),
            (EdgeKind.INHERITS, "Store", "Named"),
        },
    ),
}


@pytest.mark.parametrize("filename", sorted(CASES))
def test_language_extraction(filename):
    source, nodes, calls, imports, bases = CASES[filename]
    result = _parse(filename, source)

    assert nodes <= _nodes(result)
    assert calls <= _edges(result, EdgeKind.CALLS)
    assert imports == _imports(result)
    for kind, child, base in bases:
        assert (child, base) in _edges(result, kind)


def test_php_traits_are_flagged():
    result = _parse("a.php", "<?php\ntrait Audits {}\n")
    assert [n.extra for n in result.nodes] == [{"is_trait": True}]


def test_go_test_functions_need_a_test_file():
    result = _parse("main.go", "package main\nfunc TestLike() {}\n")
    assert _nodes(result) == {("Function", "TestLike")}


def test_c_struct_reference_is_not_a_definition():
    result = _parse("a.c", "struct point *origin;\nvoid f(struct point p) {}\n")
    assert ("Struct", "point") not in _nodes(result)


def test_inline_callback_calls_belong_to_the_enclosing_function():
    result = _parse("a.js", "function run() { items.forEach((x) => process(x)); }\n")
    assert _nodes(result) == {("Function", "run")}
    assert ("run", "process") in _edges(result, EdgeKind.CALLS)


class TestRegistry:
    def test_every_extension_has_one_parser(self):
        seen: dict[str, str] = {}
        for parser_class in PARSER_CLASSES:
            for ext in parser_class.extensions:
                assert ext not in seen, f"{ext} claimed by {seen.get(ext)} and {parser_class}"
                seen[ext] = parser_class.language

    @pytest.mark.parametrize("parser_class", PARSER_CLASSES, ids=lambda c: c.language)
    def test_grammar_ships_as_a_wheel(self, parser_class):
        # Parsing must not fall through to the language pack, which downloads
        # grammars on first use and fails offline.
        grammar = getattr(parser_class, "grammar", "") or parser_class.language
        assert grammars.get_language(grammar) is not None

    def test_tsx_grammar_ships_as_a_wheel(self):
        assert grammars.get_language("tsx") is not None

    @pytest.mark.parametrize(
        "handle, language",
        [
            ("py", "python"),
            ("rs", "rust"),
            ("golang", "go"),
            ("c#", "csharp"),
            ("tsx", "typescript"),
        ],
    )
    def test_languages_are_selected_by_name_alias_or_extension(self, handle, language):
        assert get_parser(handle).language == language

    def test_selecting_one_extension_keeps_the_rest_of_the_language(self):
        assert set(build_extension_map(["tsx"])) == {"ts", "tsx", "mts", "cts"}

    def test_unknown_language_selects_nothing(self):
        assert get_parser("cobol") is None
        assert build_extension_map(["cobol"]) == {}


def test_pipeline_indexes_a_polyglot_project(tmp_path):
    for filename, (source, *_rest) in CASES.items():
        (tmp_path / filename).write_text(source)

    pipeline = CodeCortexPipeline.from_directory(
        tmp_path, config=PipelineConfig(enable_clustering=False)
    )

    assert {p.name for p in pipeline._indexed_files} == set(CASES)
    languages = {n.language for n in pipeline.graph_store.all_nodes()}
    assert {"go", "rust", "java", "csharp", "c", "cpp", "ruby", "kotlin"} <= languages
    assert {"swift", "scala", "lua", "bash", "javascript", "typescript", "php"} <= languages
