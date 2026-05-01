defmodule ApodornotWebWeb.GlossaryTest do
  use ExUnit.Case, async: true

  alias ApodornotWebWeb.Glossary

  test "loads entries from the JSON data file at compile time" do
    keys = Glossary.keys()
    assert "fwhm" in keys
    assert "eccentricity" in keys
    assert "axis.star_quality" in keys
    assert "p50" in keys
  end

  test "lookup returns the full entry for a known term" do
    fwhm = Glossary.lookup("fwhm")
    assert fwhm["label"] =~ "FWHM"
    assert is_binary(fwhm["beginner"])
    assert is_binary(fwhm["intermediate"])
    assert is_binary(fwhm["advanced"])

    # Levels differ — beginner shouldn't equal advanced for any real entry.
    refute fwhm["beginner"] == fwhm["advanced"]
  end

  test "lookup returns nil for an unknown term" do
    assert Glossary.lookup("not_a_real_term") == nil
  end

  test "every entry has all four required fields" do
    for key <- Glossary.keys() do
      entry = Glossary.lookup(key)
      assert is_binary(entry["label"]), "missing label for #{key}"
      assert is_binary(entry["beginner"]), "missing beginner for #{key}"
      assert is_binary(entry["intermediate"]), "missing intermediate for #{key}"
      assert is_binary(entry["advanced"]), "missing advanced for #{key}"
    end
  end
end
