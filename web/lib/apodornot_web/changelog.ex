defmodule ApodornotWeb.Changelog.Parser do
  @moduledoc false

  def parse(markdown) do
    markdown
    |> String.split("\n")
    |> Enum.reduce({[], nil, nil, []}, fn line, {releases, current_date, current_cat, entries} ->
      cond do
        String.match?(line, ~r/^## \d{4}-\d{2}-\d{2}/) ->
          date = String.trim_leading(line, "## ")
          releases = flush(releases, current_date, current_cat, entries)
          {releases, date, nil, []}

        String.starts_with?(line, "### ") ->
          cat = String.trim_leading(line, "### ")
          releases = flush_section(releases, current_date, current_cat, entries)
          {releases, current_date, cat, []}

        String.match?(line, ~r/^- /) and current_date != nil ->
          text = String.trim_leading(line, "- ")
          entry = parse_entry(text)
          {releases, current_date, current_cat, entries ++ [entry]}

        true ->
          {releases, current_date, current_cat, entries}
      end
    end)
    |> then(fn {releases, date, cat, entries} -> flush(releases, date, cat, entries) end)
    |> Enum.reverse()
  end

  defp parse_entry(text) do
    case Regex.run(~r/^\*\*(.+?)\*\*[:\s]*(.*)$/, text) do
      [_, highlight, rest] -> %{text: strip_md(rest), highlight: highlight}
      _ -> %{text: strip_md(text), highlight: nil}
    end
  end

  # Strip just the inline-code markers; leave the rest of the markdown intact
  # so the LiveView can render <em>/<strong> via the inline formatter if it
  # wants to. For now we keep things simple and treat content as plain text.
  defp strip_md(text) do
    text
    |> String.replace(~r/`([^`]+)`/, "\\1")
    |> String.trim()
  end

  defp flush_section(releases, nil, _cat, _entries), do: releases

  defp flush_section(releases, date, cat, entries) when entries != [] and cat != nil do
    section = %{category: cat, entries: entries}

    case releases do
      [%{date: ^date, sections: sections} | rest] ->
        [%{date: date, sections: sections ++ [section]} | rest]

      _ ->
        [%{date: date, sections: [section]} | releases]
    end
  end

  defp flush_section(releases, _date, _cat, _entries), do: releases

  defp flush(releases, nil, _cat, _entries), do: releases

  defp flush(releases, date, cat, entries) do
    releases = flush_section(releases, date, cat, entries)

    case releases do
      [%{date: ^date} | _] -> releases
      _ -> [%{date: date, sections: []} | releases]
    end
  end
end

defmodule ApodornotWeb.Changelog do
  @moduledoc """
  Parses ``CHANGELOG.md`` at the repo root into structured data at compile
  time. Edit the markdown file and Phoenix recompiles the module via
  ``@external_resource``.

  Format:

      ## YYYY-MM-DD
      ### Category
      - **Highlight** — body text...
      - **Another** — more text...

  Categories ("Scoring & Diagnostics", "User Experience", "Reliability",
  etc.) are user-defined — anything between ``###`` markers is a section.
  """

  @changelog_path Path.expand("../../CHANGELOG.md", __DIR__)
  @external_resource @changelog_path

  @releases (
    case File.read(@changelog_path) do
      {:ok, content} -> ApodornotWeb.Changelog.Parser.parse(content)
      {:error, _} -> []
    end
  )

  @doc "Returns all changelog releases, newest first."
  def releases, do: @releases

  @doc "Returns the most recent N releases."
  def recent(n \\ 5), do: Enum.take(@releases, n)

  @doc "Returns the date string of the latest release, or nil if empty."
  def latest_date do
    case @releases do
      [%{date: date} | _] -> date
      _ -> nil
    end
  end
end
