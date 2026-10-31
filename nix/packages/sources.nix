# Source subsets (lib.fileset) so UI edits never rebuild/redeploy the speech services.
{ lib }:
let
  root = ../..;
in
{
  speechSrc = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.unions [
      (root + "/src/__init__.py")
      (root + "/src/services")
    ];
  };

  # Edge app: everything under src/ except the speech services.
  edgeSrc = lib.fileset.toSource {
    inherit root;
    fileset = lib.fileset.difference
      (lib.fileset.unions [ (root + "/src") (root + "/pyproject.toml") ])
      (root + "/src/services");
  };
}
