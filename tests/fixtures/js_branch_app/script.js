function choose(flag) {
  if (flag) {
    return "positive";
  } else {
    return "negative";
  }
}

document.getElementById("positive").addEventListener("click", function () {
  document.getElementById("result").textContent = choose(true);
});
