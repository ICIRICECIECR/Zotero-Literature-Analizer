/*
 * Trae Literature Interpretation - 设置面板脚本
 * 随 prefs-pane.xhtml 一起加载到 Zotero 设置窗口。
 * preference 属性的读写由 Zotero 设置页绑定层自动处理，
 * 这里只负责两个按钮的交互。
 */

var TraeLitInterpPrefs = {
  init: function () {
    var doc = document;
    var browse = doc.getElementById("trae-lit-interp-browsePython");
    if (browse) {
      browse.addEventListener("command", function () {
        TraeLitInterpPrefs._browsePython();
      });
    }
    var reset = doc.getElementById("trae-lit-interp-resetUpdate");
    if (reset) {
      reset.addEventListener("command", function () {
        TraeLitInterpPrefs._resetUpdate();
      });
    }
  },

  // 选择 python.exe 并写回输入框（派发 change 事件触发绑定层保存到 pref）
  _browsePython: async function () {
    try {
      var { FilePicker } = ChromeUtils.importESModule(
        "chrome://zotero/content/modules/filePicker.mjs");
      var fp = new FilePicker();
      fp.init(window, "选择 Python 可执行文件", fp.modeOpen);
      fp.appendFilter("可执行文件", "*.exe");
      var rv = await fp.show();
      if (rv !== fp.returnOK) return;
      var path = (typeof fp.file === "string") ? fp.file : fp.file.path;
      this._setInput("trae-lit-interp-pythonPath", path);
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] browse python failed: " + e);
    }
  },

  // 恢复 update.json 默认路径
  _resetUpdate: function () {
    var dataDir = Zotero.DataDirectory.dir;
    var def = PathUtils.join(dataDir, "trae-lit-interp", "update.json");
    this._setInput("trae-lit-interp-updateSource", def);
  },

  _setInput: function (id, value) {
    var input = document.getElementById(id);
    if (!input) return;
    input.value = value;
    // 触发绑定层把新值写入 Zotero.Prefs（global 命名域）
    input.dispatchEvent(new Event("change"));
  }
};
