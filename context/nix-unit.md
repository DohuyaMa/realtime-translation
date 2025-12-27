# flake-parts

[](https://github.com/hercules-ci/flake.parts-website/edit/main/site/src/options/nix-unit.md "Suggest an edit")

# [nix-unit](https://flake.parts/options/nix-unit.html#nix-unit)

Run [nix-unit](https://nix-community.github.io/nix-unit/) tests in [`checks`](https://flake.parts/options/flake-parts#opt-perSystem.checks).

See also the [complete example / template](https://nix-community.github.io/nix-unit/examples/flake-parts.html).

## [Installation](https://flake.parts/options/nix-unit.html#installation)

To use these options, add to your flake inputs:

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="language-nix hljs">nix-unit.url = "github:nix-community/nix-unit";
</code></pre>

and inside the `mkFlake`:

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="language-nix hljs">imports = [
  inputs.nix-unit.modules.flake.default
];
</code></pre>

Run `nix flake lock` and you’re set.

## [Options](https://flake.parts/options/nix-unit.html#options)

## [flake.tests](https://flake.parts/options/nix-unit.html#opt-flake.tests)

A nix-unit test suite; as [introduced in the manual](https://nix-community.github.io/nix-unit/examples/simple.html).

*Type:* lazy attribute set of raw value

*Default:* `{ }`

*Example:*

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">{
  "test integer equality is reflexive" = {
    expr = "123";
    expected = "123";
  };
  "frobnicator" = {
    "testFoo" = {
      expr = "foo";
      expected = "foo";
    };
  }
}

</code></pre>

*Declared by:*

* [nix-unit/lib/modules/flake/tests-output.nix](https://github.com/nix-community/nix-unit/blob/main/lib/modules/flake/tests-output.nix)

## [perSystem.nix-unit.enableSystemAgnostic](https://flake.parts/options/nix-unit.html#opt-perSystem.nix-unit.enableSystemAgnostic)

Copy system-agnostic tests from the `flake.tests` attribute into this system’s tests.

This ensures that the tests that are not defined in the system-specific tests are still run in `nix flake check`.

*Type:* boolean

*Default:* `true`

*Declared by:*

* [nix-unit/lib/modules/flake/system-agnostic.nix](https://github.com/nix-community/nix-unit/blob/main/lib/modules/flake/system-agnostic.nix)

## [perSystem.nix-unit.package](https://flake.parts/options/nix-unit.html#opt-perSystem.nix-unit.package)

The nix-unit package to use.

*Type:* package

*Default:* package from the `nix-unit` flake, using that flake’s `inputs.nixpkgs` (except for `follows`, etc)

*Declared by:*

* [nix-unit/lib/modules/flake/system.nix](https://github.com/nix-community/nix-unit/blob/main/lib/modules/flake/system.nix)
* [nix-unit/lib/modules.nix, via option flake.modules.flake.default](https://github.com/nix-community/nix-unit/blob/main/lib/modules.nix, via option flake.modules.flake.default)

## [perSystem.nix-unit.allowNetwork](https://flake.parts/options/nix-unit.html#opt-perSystem.nix-unit.allowNetwork)

Whether to allow network access in the nix-unit tests. This is useful for tests that depend on fetched sources, as is often the case with flake inputs. `nix-unit.inputs` may also be a solution, and tends to perform better. Both solutions can be combined.

*Type:* boolean

*Default:* `false`

*Declared by:*

* [nix-unit/lib/modules/flake/system.nix](https://github.com/nix-community/nix-unit/blob/main/lib/modules/flake/system.nix)

## [perSystem.nix-unit.inputs](https://flake.parts/options/nix-unit.html#opt-perSystem.nix-unit.inputs)

Input overrides to pass to nix-unit.

Since nix-unit will be invoked in the nix sandbox, any flake inputs that are required for the tests must be passed here.

This prevents the need to download these inputs for each run.

*Type:* attribute set of absolute path

*Default:* `{ }`

*Example:*

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">{
  inherit (inputs) nixpkgs flake-parts;
}

</code></pre>

*Declared by:*

* [nix-unit/lib/modules/flake/system.nix](https://github.com/nix-community/nix-unit/blob/main/lib/modules/flake/system.nix)

## [perSystem.nix-unit.tests](https://flake.parts/options/nix-unit.html#opt-perSystem.nix-unit.tests)

A nix-unit test suite; as [introduced in the manual](https://nix-community.github.io/nix-unit/examples/simple.html).

*Type:* lazy attribute set of raw value

*Default:* `{ }`

*Example:*

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">{
  "test integer equality is reflexive" = {
    expr = "123";
    expected = "123";
  };
  "frobnicator" = {
    "testFoo" = {
      expr = "foo";
      expected = "foo";
    };
  }
}</code></pre>
