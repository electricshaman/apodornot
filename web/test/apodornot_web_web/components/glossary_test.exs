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

  describe "linkify_html" do
    test "wraps a known phrase in a button" do
      html = Glossary.linkify_html("<p>The FWHM was 3.2 px.</p>")
      assert html =~ ~s|phx-value-term="fwhm"|
      assert html =~ ~s|>FWHM<|
    end

    test "walks into <code> so backticked metric ids in chat are clickable" do
      html = Glossary.linkify_html("<p>Your <code>fwhm_corner_excess</code> is high.</p>")
      assert html =~ ~s|phx-value-term="corner_fwhm_excess"|
      assert html =~ ~s|>fwhm_corner_excess<|
    end

    test "still skips <pre> blocks" do
      original = "<pre><code>vignetting_falloff = 0.42</code></pre>"
      assert Glossary.linkify_html(original) == original
    end

    test "links snake_case metric aliases" do
      for {alias_str, term_id} <- [
            {"vignetting_falloff", "vignetting"},
            {"psd_high_band_suppression", "hf_suppression"},
            {"snr_target_median", "snr"},
            {"noise_floor_l", "background_sigma"},
            {"color_balance_magnitude", "channel_balance"}
          ] do
        html = Glossary.linkify_html("<p><code>#{alias_str}</code></p>")
        assert html =~ ~s|phx-value-term="#{term_id}"|,
               "expected `#{alias_str}` to link to term `#{term_id}`, got: #{html}"
      end
    end
  end
end
