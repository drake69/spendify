# spendifai.rb — Homebrew Cask for Spendif.ai
#
# IMPORTANT: This file lives in the separate tap repository
#   https://github.com/drake69/homebrew-spendifai
# under the path:  Casks/spendifai.rb
#
# It is NOT part of the main code repository (drake69/spendify).
# The release pipeline (packaging/release.sh in the main repo) updates
# the `version` and `sha256` fields automatically on each release and
# commits the change to the tap repo.
#
# User installation:
#   brew tap drake69/spendifai
#   brew install --cask spendifai
#
# To submit to Homebrew Core (requires ≥75 stars, signed+notarised app,
# stable release history): see docs/release_process.md in the main repo.

cask "spendifai" do
  version "0.1.0"
  sha256 "PLACEHOLDER_SHA256_DMG"

  url "https://github.com/drake69/spendify/releases/download/v#{version}/Spendif.ai-#{version}.dmg"
  name "Spendif.ai"
  desc "Personal finance manager with local AI categorisation"
  homepage "https://github.com/drake69/spendify"

  # App bundle extracted from DMG
  app "Spendif.ai.app"

  # Post-install: create data dirs used by the application
  postflight do
    mkdir "#{Dir.home}/.spendifai"
    mkdir "#{Dir.home}/.spendifai/models"
  end

  # Gracefully quit the app before uninstall
  uninstall quit: "ai.spendif.app"

  # Remove all user data on `brew uninstall --zap`
  zap trash: [
    "~/.spendifai",
    "~/Library/Application Support/Spendif.ai",
    "~/Library/Logs/Spendif.ai",
  ]
end
