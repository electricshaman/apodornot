defmodule ApodornotWebWeb.ErrorJSONTest do
  use ApodornotWebWeb.ConnCase, async: true

  test "renders 404" do
    assert ApodornotWebWeb.ErrorJSON.render("404.json", %{}) == %{errors: %{detail: "Not Found"}}
  end

  test "renders 500" do
    assert ApodornotWebWeb.ErrorJSON.render("500.json", %{}) ==
             %{errors: %{detail: "Internal Server Error"}}
  end
end
