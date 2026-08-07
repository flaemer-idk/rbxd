{ pkgs ? import <nixpkgs> {} }:
pkgs.mkShell {
  buildInputs = with pkgs; [
    wineWow64Packages.stable
    winetricks
    cage
    cabextract unzip p7zip
    python312
    python312Packages.pip
  ];
  shellHook = ''
   echo "export WINEPREFIX="$PWD/.wine-rfd""
   echo "this is prorably for wineprefix if you want --backend wine sorry" 
   echo "export WINEDEBUG=-all"
   echo "this is for no logs idk but yeah"
   echo "[ -d "$WINEPREFIX" ] || wineboot --init"
   echo "this is wineboot init a prefix for"
   echo "guys download please this for work prorably for wineprefix"
   echo "winetricks vcrun2019"
  '';
}
