defmodule ApodornotWebWeb.UploadLiveTest do
  use ApodornotWebWeb.ConnCase
  import Phoenix.LiveViewTest

  test "upload page renders the drop zone and target type select", %{conn: conn} do
    {:ok, view, html} = live(conn, ~p"/")

    assert html =~ "Evaluate an astrophoto"
    assert html =~ "drop or click"
    assert has_element?(view, "select[name=target_type]")
    assert has_element?(view, "option[value=rosette]")
    assert has_element?(view, "option[value=auto]")
  end

  test "submit without a file shows an error", %{conn: conn} do
    {:ok, view, _html} = live(conn, ~p"/")
    rendered = render_submit(view, "submit", %{})
    assert rendered =~ "Pick an image first"
  end
end
