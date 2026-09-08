/*
 * Trae Literature Interpretation Plugin for Zotero 7+
 * bootstrap.js - Plugin lifecycle, menu integration, and main logic
 */

var TraeLitInterp = {
  _rootURI: null,
  _scriptDir: null,
  _initialized: false,

  _pluginID: "trae-lit-interp@example.com",
  _version: "1.0.0",
  _menuRegistered: false,

  init: function ({ id, version, rootURI }) {
    if (this._initialized) return;
    this._initialized = true;
    this._rootURI = rootURI;
    this._version = version || "1.0.0";

    this._setDefaults();

    var dataDir = Zotero.DataDirectory.dir;
    this._scriptDir = PathUtils.join(dataDir, "trae-lit-interp");

    this._copyScript().then(function () {
      Zotero.debug("[Trae Lit Interp] Script copied to " + this._scriptDir);
    }.bind(this)).catch(function (e) {
      Zotero.debug("[Trae Lit Interp] Failed to copy script: " + e);
    });

    this._copyUpdateManifest().then(function () {
      Zotero.debug("[Trae Lit Interp] Update manifest copied");
    }.bind(this)).catch(function (e) {
      Zotero.debug("[Trae Lit Interp] Failed to copy update manifest: " + e);
    });
  },

  _setDefaults: function () {
    var dataDir = Zotero.DataDirectory.dir;
    var defaultUpdatePath = PathUtils.join(dataDir, "trae-lit-interp", "update.json");
    var defaults = {
      "pythonPath": "python",
      "apiKey": "",
      "apiBase": "https://api.deepseek.com/v1",
      "model": "deepseek-chat",
      "updateSource": defaultUpdatePath
    };
    for (var key in defaults) {
      var prefKey = "extensions.trae-lit-interp." + key;
      if (Zotero.Prefs.get(prefKey, true) === undefined) {
        Zotero.Prefs.set(prefKey, defaults[key], true);
      }
    }
  },

  _copyScript: async function () {
    await IOUtils.makeDir(this._scriptDir, { createParent: true });
    var scriptURL = this._rootURI + "scripts/lit_interp_engine.py";
    var response = await fetch(scriptURL);
    var scriptContent = await response.text();
    var scriptPath = PathUtils.join(this._scriptDir, "lit_interp_engine.py");
    var encoded = new TextEncoder().encode(scriptContent);
    await IOUtils.write(scriptPath, encoded);
  },

  _copyUpdateManifest: async function () {
    await IOUtils.makeDir(this._scriptDir, { createParent: true });
    var updateURL = this._rootURI + "update.json";
    var response = await fetch(updateURL);
    var updateContent = await response.text();
    var updatePath = PathUtils.join(this._scriptDir, "update.json");
    var encoded = new TextEncoder().encode(updateContent);
    await IOUtils.write(updatePath, encoded);
    Zotero.debug("[Trae Lit Interp] Update manifest at: " + updatePath);
  },

  /******************* Menu Integration *******************/

  addToWindow: function (win) {
    if (this._menuRegistered) return;

    if (Zotero.MenuManager && Zotero.MenuManager.registerMenu) {
      Zotero.debug("[Trae Lit Interp] Using MenuManager API");
      this._registerMenuAPI(win);
    } else {
      Zotero.debug("[Trae Lit Interp] Falling back to legacy menu injection");
      this._registerMenuLegacy(win);
    }
  },

  _registerMenuAPI: function (win) {
    var self = this;
    try {
      Zotero.MenuManager.registerMenu({
        menuID: "trae-lit-interp",
        pluginID: this._pluginID,
        target: "main/library/item",
        menus: [
          {
            menuType: "submenu",
            label: "文献解读",
            onShowing: function (event, context) {
              var hasItems = context.items && context.items.length > 0;
              context.setVisible(hasItems);
            },
            menus: [
              {
                menuType: "menuitem",
                label: "自动生成解读 (DeepSeek API)",
                onCommand: function (event, context) {
                  self.generateAuto(win);
                }
              },
              {
                menuType: "menuitem",
                label: "导出PDF+提取图表 (手动模式)",
                onCommand: function (event, context) {
                  self.exportPDF(win);
                }
              },
              {
                menuType: "menuitem",
                label: "导入HTML报告",
                onCommand: function (event, context) {
                  self.importHTML(win);
                }
              },
              {
                menuType: "menuitem",
                label: "检查更新...",
                onCommand: function (event, context) {
                  self.checkForUpdates(win);
                }
              },
              {
                menuType: "menuitem",
                label: "设置...",
                onCommand: function (event, context) {
                  self.openSettings(win);
                }
              }
            ]
          }
        ]
      });
      this._menuRegistered = true;
      Zotero.debug("[Trae Lit Interp] Menu registered via MenuManager API");
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] MenuManager registration failed: " + e);
      this._registerMenuLegacy(win);
    }
  },

  _registerMenuLegacy: function (win) {
    var doc = win.document;
    var menuPopup = doc.getElementById("zotero-itemmenu");
    Zotero.debug("[Trae Lit Interp] Legacy: menuPopup " + (menuPopup ? "found" : "NOT FOUND"));
    if (!menuPopup) return;
    if (doc.getElementById("trae-lit-interp-menu")) return;

    var menu = doc.createXULElement("menu");
    menu.setAttribute("id", "trae-lit-interp-menu");
    menu.setAttribute("label", "文献解读");

    var subPopup = doc.createXULElement("menupopup");
    subPopup.setAttribute("id", "trae-lit-interp-popup");

    var autoItem = doc.createXULElement("menuitem");
    autoItem.setAttribute("id", "trae-lit-interp-auto");
    autoItem.setAttribute("label", "自动生成解读 (DeepSeek API)");
    autoItem.addEventListener("command", function () {
      TraeLitInterp.generateAuto(win);
    });
    subPopup.appendChild(autoItem);

    var exportItem = doc.createXULElement("menuitem");
    exportItem.setAttribute("id", "trae-lit-interp-export");
    exportItem.setAttribute("label", "导出PDF+提取图表 (手动模式)");
    exportItem.addEventListener("command", function () {
      TraeLitInterp.exportPDF(win);
    });
    subPopup.appendChild(exportItem);

    var importItem = doc.createXULElement("menuitem");
    importItem.setAttribute("id", "trae-lit-interp-import");
    importItem.setAttribute("label", "导入HTML报告");
    importItem.addEventListener("command", function () {
      TraeLitInterp.importHTML(win);
    });
    subPopup.appendChild(importItem);

    var sep = doc.createXULElement("menuseparator");
    subPopup.appendChild(sep);

    var updateItem = doc.createXULElement("menuitem");
    updateItem.setAttribute("id", "trae-lit-interp-update");
    updateItem.setAttribute("label", "检查更新...");
    updateItem.addEventListener("command", function () {
      TraeLitInterp.checkForUpdates(win);
    });
    subPopup.appendChild(updateItem);

    var settingsItem = doc.createXULElement("menuitem");
    settingsItem.setAttribute("id", "trae-lit-interp-settings");
    settingsItem.setAttribute("label", "设置...");
    settingsItem.addEventListener("command", function () {
      TraeLitInterp.openSettings(win);
    });
    subPopup.appendChild(settingsItem);

    menu.appendChild(subPopup);
    menuPopup.appendChild(menu);
    this._menuRegistered = true;
  },

  removeFromWindow: function (win) {
    var doc = win.document;
    var menu = doc.getElementById("trae-lit-interp-menu");
    if (menu) menu.remove();
  },

  /******************* Core Logic *******************/

  _getSelectedPDF: function (win) {
    var zoteroPane = win.ZoteroPane || Zotero.getActiveZoteroPane();
    var items = zoteroPane.getSelectedItems();
    if (!items || items.length === 0) return null;

    var item = items[0];

    if (item.isRegularItem()) {
      var attachments = item.getAttachments();
      for (var i = 0; i < attachments.length; i++) {
        var att = Zotero.Items.get(attachments[i]);
        if (att.attachmentContentType === "application/pdf") {
          return { parentItem: item, attachment: att };
        }
      }
    }

    if (item.isAttachment() && item.attachmentContentType === "application/pdf") {
      var parent = item.getParent();
      return { parentItem: parent || item, attachment: item };
    }

    return null;
  },

  generateAuto: async function (win) {
    var sel = this._getSelectedPDF(win);
    if (!sel) {
      this._notify(win, "请选中一个包含PDF附件的条目", "error");
      return;
    }

    var apiKey = Zotero.Prefs.get("extensions.trae-lit-interp.apiKey", true);
    if (!apiKey) {
      this._notify(win, "未设置 DeepSeek API Key，请在设置中配置", "error");
      this.openSettings(win);
      return;
    }

    var progressWin = this._showProgress(win, "正在生成文献解读...");

    try {
      var pdfPath = await sel.attachment.getFilePathAsync();
      var outputPath = PathUtils.join(this._scriptDir, "output_" + Date.now() + ".html");

      var args = [
        PathUtils.join(this._scriptDir, "lit_interp_engine.py"),
        "--pdf", pdfPath,
        "--output", outputPath,
        "--mode", "auto",
        "--api-key", apiKey,
        "--api-base", Zotero.Prefs.get("extensions.trae-lit-interp.apiBase", true),
        "--model", Zotero.Prefs.get("extensions.trae-lit-interp.model", true)
      ];

      var exitCode = await this._runPython(args);

      if (exitCode !== 0) {
        progressWin.close();
        this._notify(win, "Python脚本执行失败 (exit " + exitCode + ")", "error");
        return;
      }

      var htmlContent = await this._readFile(outputPath);
      progressWin.close();

      await this._createNote(sel.parentItem, htmlContent, "文献解读");
      this._notify(win, "文献解读已生成并保存为笔记", "success");
    } catch (e) {
      progressWin.close();
      Zotero.debug("[Trae Lit Interp] Error: " + e);
      this._notify(win, "生成失败: " + e.message, "error");
    }
  },

  exportPDF: async function (win) {
    var sel = this._getSelectedPDF(win);
    if (!sel) {
      this._notify(win, "请选中一个包含PDF附件的条目", "error");
      return;
    }

    var progressWin = this._showProgress(win, "正在提取PDF内容和图表...");

    try {
      var pdfPath = await sel.attachment.getFilePathAsync();
      var workDir = PathUtils.join(this._scriptDir, "work_" + Date.now());
      await IOUtils.makeDir(workDir, { createParent: true });

      var outputPath = PathUtils.join(workDir, "template.html");
      var promptPath = PathUtils.join(workDir, "prompt.txt");

      var args = [
        PathUtils.join(this._scriptDir, "lit_interp_engine.py"),
        "--pdf", pdfPath,
        "--output", outputPath,
        "--mode", "manual",
        "--prompt-file", promptPath
      ];

      var exitCode = await this._runPython(args);
      progressWin.close();

      if (exitCode !== 0) {
        this._notify(win, "Python脚本执行失败 (exit " + exitCode + ")", "error");
        return;
      }

      this._openFolder(workDir);
      this._notify(win, "PDF内容和图表已提取到: " + workDir, "success");
    } catch (e) {
      progressWin.close();
      this._notify(win, "导出失败: " + e.message, "error");
    }
  },

  importHTML: async function (win) {
    var sel = this._getSelectedPDF(win);
    var parentItem = sel ? sel.parentItem : null;

    if (!parentItem) {
      var zoteroPane = win.ZoteroPane || Zotero.getActiveZoteroPane();
      var items = zoteroPane.getSelectedItems();
      if (items && items.length > 0) {
        parentItem = items[0];
      }
    }

    if (!parentItem) {
      this._notify(win, "请先选中一个文献条目", "error");
      return;
    }

    var fp = Components.classes["@mozilla.org/filepicker;1"]
      .createInstance(Components.interfaces.nsIFilePicker);
    fp.init(win, "选择文献解读HTML文件", Components.interfaces.nsIFilePicker.modeOpen);
    fp.appendFilter("HTML", "*.html;*.htm");

    var result = await new Promise(function (resolve) {
      fp.open(resolve);
    });

    if (result !== Components.interfaces.nsIFilePicker.returnOK) return;

    try {
      var htmlContent = await this._readFile(fp.file.path);
      await this._createNote(parentItem, htmlContent, "文献解读");
      this._notify(win, "HTML报告已导入为笔记", "success");
    } catch (e) {
      this._notify(win, "导入失败: " + e.message, "error");
    }
  },

  /******************* Settings Dialog *******************/

  openSettings: function (win) {
    var apiKey = Zotero.Prefs.get("extensions.trae-lit-interp.apiKey", true) || "";
    var pythonPath = Zotero.Prefs.get("extensions.trae-lit-interp.pythonPath", true) || "python";
    var apiBase = Zotero.Prefs.get("extensions.trae-lit-interp.apiBase", true) || "https://api.deepseek.com/v1";
    var model = Zotero.Prefs.get("extensions.trae-lit-interp.model", true) || "deepseek-chat";
    var updateSource = Zotero.Prefs.get("extensions.trae-lit-interp.updateSource", true) || "";

    var prompts = Components.classes["@mozilla.org/embedcomp/prompt-service;1"]
      .createInstance(Components.interfaces.nsIPromptService);

    var input = { value: apiKey };
    var result = prompts.prompt(win, "DeepSeek API Key", "请输入 DeepSeek API Key（留空则使用手动模式）:", input, null, {});
    if (result) {
      Zotero.Prefs.set("extensions.trae-lit-interp.apiKey", input.value, true);
    }

    var pyInput = { value: pythonPath };
    result = prompts.prompt(win, "Python 路径", "请输入 Python 路径（如 python, C:\\Python311\\python.exe）:", pyInput, null, {});
    if (result) {
      Zotero.Prefs.set("extensions.trae-lit-interp.pythonPath", pyInput.value, true);
    }

    var modelInput = { value: model };
    result = prompts.prompt(win, "模型名称", "请输入模型名称（如 deepseek-chat）:", modelInput, null, {});
    if (result) {
      Zotero.Prefs.set("extensions.trae-lit-interp.model", modelInput.value, true);
    }

    var baseInput = { value: apiBase };
    result = prompts.prompt(win, "API Base URL", "请输入 API Base URL:", baseInput, null, {});
    if (result) {
      Zotero.Prefs.set("extensions.trae-lit-interp.apiBase", baseInput.value, true);
    }

    var updateInput = { value: updateSource };
    result = prompts.prompt(win, "更新源路径", "请输入本地 update.json 路径\n（留空使用默认位置）:", updateInput, null, {});
    if (result) {
      if (updateInput.value.trim() === "") {
        var dataDir = Zotero.DataDirectory.dir;
        updateInput.value = PathUtils.join(dataDir, "trae-lit-interp", "update.json");
      }
      Zotero.Prefs.set("extensions.trae-lit-interp.updateSource", updateInput.value, true);
    }
  },

  /******************* Update Check *******************/

  checkForUpdates: async function (win) {
    var updateSource = Zotero.Prefs.get("extensions.trae-lit-interp.updateSource", true) || "";
    if (!updateSource) {
      var dataDir = Zotero.DataDirectory.dir;
      updateSource = PathUtils.join(dataDir, "trae-lit-interp", "update.json");
    }

    try {
      var fileExists = await IOUtils.exists(updateSource);
      if (!fileExists) {
        this._notify(win, "更新清单不存在: " + updateSource + "\n请在设置中配置正确路径", "error");
        return;
      }

      var content = await IOUtils.readUTF8(updateSource);
      var updateData = JSON.parse(content);

      var addonInfo = updateData.addons && updateData.addons[this._pluginID];
      if (!addonInfo || !addonInfo.updates || addonInfo.updates.length === 0) {
        this._notify(win, "当前版本 " + this._version + " 已是最新", "success");
        return;
      }

      var latest = addonInfo.updates[0];
      var latestVersion = latest.version;

      if (this._compareVersions(latestVersion, this._version) > 0) {
        var prompts = Components.classes["@mozilla.org/embedcomp/prompt-service;1"]
          .createInstance(Components.interfaces.nsIPromptService);
        var doUpdate = prompts.confirm(win,
          "发现新版本",
          "当前版本: " + this._version + "\n最新版本: " + latestVersion +
          "\n\n更新链接:\n" + latest.update_link +
          "\n\n点击确定打开下载页面");
        if (doUpdate && latest.update_link) {
          Zotero.launchURL(latest.update_link);
        }
      } else {
        this._notify(win, "当前版本 " + this._version + " 已是最新", "success");
      }
    } catch (e) {
      this._notify(win, "检查更新失败: " + e.message, "error");
    }
  },

  _compareVersions: function (a, b) {
    var partsA = a.split(".");
    var partsB = b.split(".");
    var len = Math.max(partsA.length, partsB.length);
    for (var i = 0; i < len; i++) {
      var numA = parseInt(partsA[i] || "0", 10);
      var numB = parseInt(partsB[i] || "0", 10);
      if (numA > numB) return 1;
      if (numA < numB) return -1;
    }
    return 0;
  },

  /******************* Utilities *******************/

  _runPython: function (args) {
    var pythonPath = Zotero.Prefs.get("extensions.trae-lit-interp.pythonPath", true) || "python";

    return new Promise(function (resolve, reject) {
      try {
        var file = Components.classes["@mozilla.org/file/local;1"]
          .createInstance(Components.interfaces.nsIFile);
        file.initWithPath(pythonPath);

        var process = Components.classes["@mozilla.org/process/util;1"]
          .createInstance(Components.interfaces.nsIProcess);
        process.init(file);

        process.runAsync(args, args.length, {
          observe: function (subject, topic) {
            if (topic === "process-finished") {
              resolve(process.exitValue);
            } else if (topic === "process-failed") {
              reject(new Error("Process failed to start"));
            }
          }
        });
      } catch (e) {
        reject(e);
      }
    });
  },

  _readFile: async function (path) {
    var bytes = await IOUtils.read(path);
    return new TextDecoder().decode(bytes);
  },

  _createNote: async function (parentItem, htmlContent, title) {
    var note = new Zotero.Item("note");
    note.libraryID = parentItem.libraryID;
    note.parentID = parentItem.id;
    note.setNote('<h1>' + title + '</h1>' + htmlContent);
    await note.saveTx();
    return note;
  },

  _showProgress: function (win, message) {
    var progressWin = new Zotero.ProgressWindow({ closeOnClick: false });
    progressWin.changeHeadline("Trae 文献解读");
    progressWin.addDescription(message);
    progressWin.show();
    return progressWin;
  },

  _notify: function (win, message, type) {
    if (type === "error") {
      var ps = Components.classes["@mozilla.org/embedcomp/prompt-service;1"]
        .getService(Components.interfaces.nsIPromptService);
      ps.alert(win, "Trae 文献解读", message);
    } else {
      var progressWin = new Zotero.ProgressWindow({ closeOnClick: true });
      progressWin.changeHeadline("Trae 文献解读");
      progressWin.addDescription(message);
      progressWin.startCloseTimer(4000);
    }
  },

  _openFolder: function (path) {
    try {
      var file = Components.classes["@mozilla.org/file/local;1"]
        .createInstance(Components.interfaces.nsIFile);
      file.initWithPath(path);
      file.reveal();
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] Cannot open folder: " + e);
    }
  }
};

/******************* Bootstrap Lifecycle (Zotero 7+) *******************/

function install({ id, version, rootURI }) {
  Zotero.debug("[Trae Lit Interp] Installed " + version);
}

async function startup({ id, version, rootURI }) {
  Zotero.debug("[Trae Lit Interp] Starting up " + version);
  try {
    TraeLitInterp.init({ id: id, version: version, rootURI: rootURI });
  } catch (e) {
    Zotero.debug("[Trae Lit Interp] Startup error: " + e);
  }
}

function onMainWindowLoad({ window }) {
  try {
    TraeLitInterp.addToWindow(window);
  } catch (e) {
    Zotero.debug("[Trae Lit Interp] onMainWindowLoad error: " + e);
  }
}

function onMainWindowUnload({ window }) {
  try {
    TraeLitInterp.removeFromWindow(window);
  } catch (e) {
    Zotero.debug("[Trae Lit Interp] onMainWindowUnload error: " + e);
  }
}

function shutdown() {
  Zotero.debug("[Trae Lit Interp] Shutting down");
  if (TraeLitInterp._menuRegistered && Zotero.MenuManager && Zotero.MenuManager.unregisterMenu) {
    try {
      Zotero.MenuManager.unregisterMenu("trae-lit-interp");
    } catch (e) {
      Zotero.debug("[Trae Lit Interp] unregisterMenu failed: " + e);
    }
  }
  TraeLitInterp._initialized = false;
  TraeLitInterp._menuRegistered = false;
}

function uninstall() {
  Zotero.debug("[Trae Lit Interp] Uninstalled");
}
