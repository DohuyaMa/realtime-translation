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
      (root + "/src/segmentation")
    ];
  };
}
