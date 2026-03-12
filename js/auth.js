/* =======================================================
   認證模組 (Auth)
======================================================= */
import { doc, getDoc } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-firestore.js";
import { onAuthStateChanged, signOut, signInWithEmailAndPassword, setPersistence, browserLocalPersistence } from "https://www.gstatic.com/firebasejs/10.14.1/firebase-auth.js";
import { db, auth } from './firebase-init.js';
import { state } from './state.js';
import { setupQtySelectByLevel } from './search.js';
import { updateUserDisplay } from './search.js';
import { preloadDriveModelData, preloadProducts } from './data.js';

function isLocalDataMode() {
  return window.__USE_LOCAL_DB__ === true || new URLSearchParams(window.location.search).get("local") === "1";
}

function isLingdongAdmin(email) {
  const e = String(email || "").trim().toLowerCase();
  if (!e) return false;
  const localPart = e.split("@")[0];
  if (localPart === "kuo.tinghow") return true;
  const admins = new Set(["kuo.tinghow@gmail.com", "kuo.tinghow@kinyo.com"]);
  return admins.has(e);
}

function toLevel(v) {
  const n = Number(v);
  if (!Number.isFinite(n)) return 0;
  return Math.max(0, Math.floor(n));
}

async function loadUserPermissionData(user) {
  const emailRaw = String(user?.email || "").trim();
  const emailLower = emailRaw.toLowerCase();
  const uid = String(user?.uid || "").trim();
  const candidateIds = Array.from(new Set([emailLower, emailRaw, uid])).filter(Boolean);

  for (const id of candidateIds) {
    const userDoc = await getDoc(doc(db, "Users", id));
    if (userDoc.exists()) {
      return userDoc.data() || null;
    }
  }
  return null;
}

/* 權限更新 */
export function updatePermissions() {
    const importProductBtn = document.getElementById("importProductBtn");
    const exportHistoryBtn = document.getElementById("exportHistoryBtn");
    const stockFilter = document.getElementById("stockFilter");
    const setHotBtn = document.getElementById("setHotBtn");

    if (state.userLevel >= 4) {
        if(importProductBtn) importProductBtn.style.display = 'inline-block'; 
        if(exportHistoryBtn) exportHistoryBtn.style.display = 'inline-block';
        if(setHotBtn) setHotBtn.style.display = 'inline-block'; 
    } else {
        if(importProductBtn) importProductBtn.style.display = 'none'; 
        if(exportHistoryBtn) exportHistoryBtn.style.display = 'none';
        if(setHotBtn) setHotBtn.style.display = 'none';
    }

    // 需求：庫存篩選暫時隱藏
    if (stockFilter) stockFilter.style.display = 'none';
}

/* 登入按鈕 */
export function setupLoginButton() {
  const doLoginBtn = document.getElementById("doLoginBtn");
  const loginEmail = document.getElementById("loginEmail");
  const loginPassword = document.getElementById("loginPassword");
  const loginError = document.getElementById("loginError");

  if (!doLoginBtn) return;

  const triggerLogin = async () => {
    let email = loginEmail.value.trim();
    const password = loginPassword.value.trim();
    
    if (email && !email.includes('@')) {
        email += '@kinyo.com';
    }

    if(!email || !password) {
      loginError.textContent = "請輸入帳號和密碼";
      loginError.style.display = "block";
      return;
    }

    loginError.style.display = "none";
    doLoginBtn.disabled = true;
    doLoginBtn.textContent = "登入中...";

    try {
      await setPersistence(auth, browserLocalPersistence);
      await signInWithEmailAndPassword(auth, email, password);
    } catch (error) {
      if (error.message && error.message.includes("message channel closed")) {
          console.warn("Ignored non-fatal auth error:", error);
          doLoginBtn.disabled = false;
          doLoginBtn.textContent = "登入 (請重試)";
          return;
      }
      
      console.error(error);
      loginError.textContent = "登入失敗：請檢查帳號密碼";
      loginError.style.display = "block";
      doLoginBtn.disabled = false;
      doLoginBtn.textContent = "登入";
    }
  };

  doLoginBtn.onclick = triggerLogin;

  const isLoginOverlayVisible = () => {
    const loginOverlay = document.getElementById("loginOverlay");
    if (!loginOverlay) return false;
    return loginOverlay.style.display !== "none";
  };

  const onEnterKey = (e) => {
    if (e.key === "Enter" || e.code === "NumpadEnter") {
      e.preventDefault();
      triggerLogin();
    }
  };
  if (loginEmail) loginEmail.addEventListener("keydown", onEnterKey);
  if (loginPassword) loginPassword.addEventListener("keydown", onEnterKey);
  if (loginEmail) loginEmail.addEventListener("keyup", onEnterKey);
  if (loginPassword) loginPassword.addEventListener("keyup", onEnterKey);

  // 保險：當登入覆蓋層顯示時，全域 Enter 也可直接登入
  document.addEventListener("keydown", (e) => {
    if (!isLoginOverlayVisible()) return;
    if (e.key === "Enter" || e.code === "NumpadEnter") {
      e.preventDefault();
      triggerLogin();
    }
  });
}

/* 登出按鈕 */
export function setupLogoutButton() {
  const logoutBtn = document.getElementById("logoutBtn");
  if (!logoutBtn) return;

  logoutBtn.onclick = () => {
    if(confirm("確定要登出嗎？")) {
        signOut(auth).then(() => {
            // 保持在當前頁，onAuthStateChanged 會自動顯示登入覆蓋層
            const loginOverlay = document.getElementById("loginOverlay");
            if (loginOverlay) loginOverlay.style.display = "flex";
        }).catch((error) => {
            console.error("登出錯誤:", error);
            alert("登出發生錯誤，請重新整理網頁");
        });
    }
  };
}

/* Auth State 監聽 */
export function setupAuthListener() {
  if (isLocalDataMode()) {
    const loginOverlay = document.getElementById("loginOverlay");
    const logoutBtn = document.getElementById("logoutBtn");
    if (loginOverlay) loginOverlay.style.display = "none";
    if (logoutBtn) logoutBtn.style.display = "none";

    state.currentUserEmail = "lingdong-local";
    state.originalUserLevel = 4;
    state.userLevel = 4;
    state.currentUserVipConfig = null;
    state.isGroupBuyUser = false;

    updateUserDisplay("normal");
    setupQtySelectByLevel();
    updatePermissions();
    window.dispatchEvent(new CustomEvent("level-state-changed"));

    Promise.resolve()
      .then(() => preloadDriveModelData())
      .then(() => preloadProducts())
      .catch((e) => {
        console.error("Local bootstrap error:", e);
        alert("本地資料模式初始化失敗");
      });
    return;
  }

  onAuthStateChanged(auth, async (user) => {
    const loginOverlay = document.getElementById("loginOverlay");
    const logoutBtn = document.getElementById("logoutBtn");

    if (!user) {
      state.originalUserLevel = 0;
      state.userLevel = 0;
      if(loginOverlay) loginOverlay.style.display = "flex";
      window.dispatchEvent(new CustomEvent("level-state-changed"));
      return;
    }
    
    if(loginOverlay) loginOverlay.style.display = "none";

    state.currentUserEmail = (user.email || "").trim();
    state.userLevel = 0; 
    state.originalUserLevel = 0;
    state.currentUserVipConfig = null;
    state.isGroupBuyUser = false;

    // 管理員 fallback：若 Users 沒建檔仍可保底進入管理權限
    if (isLingdongAdmin(state.currentUserEmail)) {
      state.originalUserLevel = 4;
      state.userLevel = 4;
    }

    try {
        const userData = await loadUserPermissionData(user);

        if (userData) {
          state.originalUserLevel = toLevel(userData.level);
          state.userLevel = state.originalUserLevel;
          if (userData.groupBuy === true) {
            state.isGroupBuyUser = true;
          }
          if (userData.vipColumn) {
            state.currentUserVipConfig = {
              column: userData.vipColumn,
              name: userData.vipName || 'VIP客戶'
            };
          }
        } else {
          // 無 Users 檔案時保留 admin fallback，其他帳號為 0
          state.originalUserLevel = Math.max(state.originalUserLevel, 0);
          state.userLevel = state.originalUserLevel;
        }
    } catch (e) {
        console.error("Auth Error:", e);
        // 讀取失敗時只保留 admin fallback，不再強制全員 L4
        state.originalUserLevel = Math.max(state.originalUserLevel, 0);
        state.userLevel = state.originalUserLevel;
    }

    if (state.currentUserEmail.toLowerCase() === 'show@kinyo.com') {
      state.originalUserLevel = 0;
      state.userLevel = 0;
    }

    updateUserDisplay('normal');
    logoutBtn.style.display = "inline-block";

    setupQtySelectByLevel();
    updatePermissions();
    window.dispatchEvent(new CustomEvent("level-state-changed"));

    await preloadDriveModelData();
    await preloadProducts();
  });
}
