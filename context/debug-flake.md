# flake-parts

[](https://github.com/hercules-ci/flake.parts-website/edit/main/site/src/debug.md "Suggest an edit")

# [Explore and debug option values](https://flake.parts/debug.html#explore-and-debug-option-values)

Sometimes the public interface of a flake is not enough. To inspect all option values, you can enable [`debug`](https://flake.parts/options/flake-parts#opt-debug) and explore otherwise private values with the repl.

## [Start debugging](https://flake.parts/debug.html#start-debugging)

1. Add `debug = true;` Example:
   <pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="language-nix hljs">{
     debug = true;

     systems = /* ... */;
     perSystem = /* ... */;
   }
   </code></pre>
2. Load the flake
   <pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">$ nix repl
   nix-repl> :lf .

   </code></pre>

## [Inspect the perSystem configuration for your machine](https://flake.parts/debug.html#inspect-the-persystem-configuration-for-your-machine)

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">nix-repl> currentSystem.allModuleArgs.pkgs.stdenv.hostPlatform.system
"x86_64-linux"

</code></pre>

## [Inspect the perSystem configuration for a different system type](https://flake.parts/debug.html#inspect-the-persystem-configuration-for-a-different-system-type)

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">nix-repl> debug.allSystems.armv7l-linux.allModuleArgs.pkgs.stdenv.hostPlatform.system
"armv7l-linux"

</code></pre>

## [Inspect a top level option](https://flake.parts/debug.html#inspect-a-top-level-option)

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">nix-repl> debug.systems
[ "x86_64-linux" "aarch64-darwin" ]

</code></pre>

## [Where is a per system value defined?](https://flake.parts/debug.html#where-is-a-per-system-value-defined)

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">nix-repl> currentSystem.options.pre-commit.settings.files
[ "/nix/store/pqp5kwdihyyymfnqq9sk9jsm9xw2lw6s-source/dev-module.nix, via option perSystem" "/nix/store/4wl7k0dp7cjyc4nxy5cm9wdb8jshlg0j-source/flake-module.nix" ]

</code></pre>

## [Where is a top level value defined?](https://flake.parts/debug.html#where-is-a-top-level-value-defined)

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">nix-repl> debug.options.system.files
[ "/nix/store/3na6c6mmyw2yf5chzwwwrp54b8yf96ry-source/flake.nix" ]

</code></pre>

## [Where is a top level option declared?](https://flake.parts/debug.html#where-is-a-top-level-option-declared)

<pre><div class="buttons"><button class="clip-button" title="Copy to clipboard" aria-label="Copy to clipboard"><i class="tooltiptext"></i></button></div><code class="hljs">nix-repl> debug.options.systems.declarations
[ "/nix/store/3na6c6mmyw2yf5chzwwwrp54b8yf96ry-source/modules/perSystem.nix" ]

</code></pre>
