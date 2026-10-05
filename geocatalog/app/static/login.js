const error = new URLSearchParams(location.search).get("error");
if (error) {
  const element = document.querySelector("#login-error");
  element.textContent = "Неверное имя пользователя или пароль.";
  element.hidden = false;
}
