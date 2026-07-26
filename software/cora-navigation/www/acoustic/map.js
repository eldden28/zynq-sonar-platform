(() => {
  "use strict";

  const EARTH_RADIUS_M = 6378137.0;
  const DEG_PER_RADIAN = 180.0 / Math.PI;

  class CoraNavigationMap {
    constructor(options = {}) {
      this.notice = options.notice || (() => {});
      this.follow = false;
      this.initialMissionFit = false;
      this.origin = { latitude: 41.2250, longitude: -77.0445 };
      this.lastAcousticTimestamp = -1;
      this.activeBase = null;
      this.satelliteLayer = null;
      this.baseSwitchToken = 0;

      this.map = L.map("navMap", {
        preferCanvas: true,
        zoomControl: true,
        attributionControl: true,
      }).setView([this.origin.latitude, this.origin.longitude], 14);

      this.streetLayer = L.tileLayer(
        "https://tile.openstreetmap.org/{z}/{x}/{y}.png",
        {
          maxZoom: 19,
          attribution:
            '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
        },
      );
      this.localLayer = L.layerGroup();

      this.truthTrack = L.polyline([], {
        color: "#738098",
        weight: 2,
        opacity: 0.85,
      }).addTo(this.map);
      this.insTrack = L.polyline([], {
        color: "#4ee4d2",
        weight: 3,
        opacity: 0.95,
      }).addTo(this.map);
      this.dvlTrack = L.polyline([], {
        color: "#7be495",
        weight: 3,
        opacity: 0.9,
        dashArray: "2 7",
      }).addTo(this.map);
      this.missionLayer = L.layerGroup().addTo(this.map);
      this.transponderLayer = L.layerGroup().addTo(this.map);
      this.acousticFixLayer = L.layerGroup().addTo(this.map);
      this.uncertaintyLayer = L.layerGroup().addTo(this.map);

      this.truthMarker = L.circleMarker(
        [this.origin.latitude, this.origin.longitude],
        {
          radius: 5,
          color: "#c4cede",
          fillColor: "#738098",
          fillOpacity: 0.9,
          weight: 1,
        },
      ).addTo(this.map).bindTooltip("Simulation truth");

      this.vehicleMarker = L.marker(
        [this.origin.latitude, this.origin.longitude],
        {
          zIndexOffset: 1000,
          icon: L.divIcon({
            className: "vehicle-icon",
            html: '<div class="vehicle-arrow"></div>',
            iconSize: [31, 31],
            iconAnchor: [15.5, 15.5],
          }),
        },
      ).addTo(this.map).bindTooltip("Fused INS");

      L.control.layers(
        null,
        {
          "Fused INS": this.insTrack,
          "Simulation truth": this.truthTrack,
          "DVL lock samples": this.dvlTrack,
          "Mission plan": this.missionLayer,
          Transponders: this.transponderLayer,
          "Acoustic fixes": this.acousticFixLayer,
          "2σ uncertainty": this.uncertaintyLayer,
        },
        { collapsed: true },
      ).addTo(this.map);

      const legend = L.control({ position: "bottomleft" });
      legend.onAdd = () => {
        const element = L.DomUtil.create("div", "map-legend");
        element.innerHTML =
          '<div><i class="legend-ins"></i>Fused INS</div>' +
          '<div><i class="legend-truth"></i>Simulation truth</div>' +
          '<div><i class="legend-dvl"></i>DVL lock samples</div>' +
          '<div><i class="legend-acoustic"></i>Acoustic fixes</div>';
        return element;
      };
      legend.addTo(this.map);

      this.map.on("dragstart zoomstart", () => this.setFollow(false));
      this.bindControls();
      this.switchBase(this.savedBase());
    }

    storageGet(key, fallback = "") {
      try {
        return localStorage.getItem(key) || fallback;
      } catch (_) {
        return fallback;
      }
    }

    storageSet(key, value) {
      try {
        if (value) localStorage.setItem(key, value);
        else localStorage.removeItem(key);
      } catch (_) {
        this.notice("This browser did not allow map settings to be saved.");
      }
    }

    savedBase() {
      const value = this.storageGet("cora-navigation-basemap", "street");
      return ["street", "satellite", "local"].includes(value)
        ? value
        : "street";
    }

    bindControls() {
      const base = document.getElementById("basemapControl");
      const follow = document.getElementById("mapFollow");
      const fit = document.getElementById("mapFit");
      const key = document.getElementById("maptilerKey");
      const save = document.getElementById("saveMapKey");
      const clear = document.getElementById("clearMapKey");

      key.value = this.storageGet("cora-maptiler-key");
      base.value = this.savedBase();
      base.addEventListener("change", () => this.switchBase(base.value));
      follow.addEventListener("click", () => this.setFollow(!this.follow));
      fit.addEventListener("click", () => this.fitMission());
      save.addEventListener("click", () => {
        const value = key.value.trim();
        this.storageSet("cora-maptiler-key", value);
        const oldSatellite = this.satelliteLayer;
        if (oldSatellite && this.map.hasLayer(oldSatellite)) {
          this.map.removeLayer(oldSatellite);
        }
        this.satelliteLayer = null;
        this.notice(
          value
            ? "Satellite key saved on this browser."
            : "Enter a MapTiler key before saving.",
        );
        if (value) this.switchBase("satellite");
      });
      clear.addEventListener("click", () => {
        key.value = "";
        this.storageSet("cora-maptiler-key", "");
        const oldSatellite = this.satelliteLayer;
        if (oldSatellite && this.map.hasLayer(oldSatellite)) {
          this.map.removeLayer(oldSatellite);
        }
        this.satelliteLayer = null;
        if (this.activeBase === "satellite") this.switchBase("street");
        this.notice("Satellite key removed from this browser.");
      });
    }

    setFollow(enabled) {
      this.follow = enabled;
      const button = document.getElementById("mapFollow");
      button.textContent = `Follow: ${enabled ? "ON" : "OFF"}`;
      button.classList.toggle("warn", !enabled);
      if (enabled && this.vehicleMarker) {
        this.map.panTo(this.vehicleMarker.getLatLng(), {
          animate: true,
          duration: 0.25,
        });
      }
    }

    satellite() {
      if (this.satelliteLayer) return this.satelliteLayer;
      const key = this.storageGet("cora-maptiler-key");
      if (!key) return null;
      this.satelliteLayer = L.tileLayer(
        "https://api.maptiler.com/maps/satellite-v4/256/{z}/{x}/{y}.jpg?key=" +
          encodeURIComponent(key),
        {
          maxZoom: 20,
          tileSize: 256,
          crossOrigin: true,
          attribution:
            '&copy; <a href="https://www.maptiler.com/copyright/">MapTiler</a>',
        },
      );
      return this.satelliteLayer;
    }

    activateBase(name, layer) {
      for (const candidate of [
        this.streetLayer,
        this.localLayer,
        this.satelliteLayer,
      ]) {
        if (candidate && candidate !== layer && this.map.hasLayer(candidate)) {
          this.map.removeLayer(candidate);
        }
      }
      if (!this.map.hasLayer(layer)) layer.addTo(this.map);
      layer.bringToBack?.();
      this.activeBase = name;
      const select = document.getElementById("basemapControl");
      select.value = name;
      this.storageSet("cora-navigation-basemap", name);
      this.map.getContainer().classList.toggle("local-grid", name === "local");
    }

    switchBase(name) {
      const select = document.getElementById("basemapControl");
      let layer;
      if (name === "satellite") {
        layer = this.satellite();
        if (!layer) {
          select.value = this.activeBase || "street";
          document.getElementById("mapKeySettings").open = true;
          this.notice(
            "Satellite view needs a MapTiler API key. Add it under Satellite setup.",
          );
          return;
        }
        if (this.activeBase === "satellite" && this.map.hasLayer(layer)) return;

        // Keep the working background visible until the first imagery tile
        // arrives. A rejected key or service error must never leave a black map.
        const previousName =
          this.activeBase && this.activeBase !== "satellite"
            ? this.activeBase
            : "street";
        const previousLayer =
          previousName === "local" ? this.localLayer : this.streetLayer;
        if (!this.map.hasLayer(previousLayer)) previousLayer.addTo(this.map);
        layer.addTo(this.map);
        layer.bringToFront?.();
        select.value = "satellite";

        const token = ++this.baseSwitchToken;
        let fallbackTimer = null;
        const cleanup = () => {
          layer.off("tileload", loaded);
          layer.off("tileerror", failed);
          if (fallbackTimer !== null) window.clearTimeout(fallbackTimer);
        };
        const loaded = () => {
          if (token !== this.baseSwitchToken) return;
          cleanup();
          this.activateBase("satellite", layer);
          this.notice("Satellite imagery loaded.");
        };
        const failed = () => {
          if (token !== this.baseSwitchToken || fallbackTimer !== null) return;
          fallbackTimer = window.setTimeout(() => {
            if (token !== this.baseSwitchToken) return;
            cleanup();
            if (this.map.hasLayer(layer)) this.map.removeLayer(layer);
            this.satelliteLayer = null;
            this.activateBase(previousName, previousLayer);
            this.notice(
              "Satellite imagery could not load. Restored the previous map; check the MapTiler key and its allowed origins.",
            );
          }, 1200);
        };
        layer.on("tileload", loaded);
        layer.on("tileerror", failed);
        return;
      } else if (name === "local") {
        layer = this.localLayer;
      } else {
        name = "street";
        layer = this.streetLayer;
      }

      ++this.baseSwitchToken;
      this.activateBase(name, layer);
    }

    updateOrigin(status) {
      const truth = status.truth;
      if (!truth?.position_ned_m || !Number.isFinite(truth.latitude_deg)) return;
      const north = truth.position_ned_m[0];
      const east = truth.position_ned_m[1];
      const latitude =
        truth.latitude_deg - (north / EARTH_RADIUS_M) * DEG_PER_RADIAN;
      const longitude =
        truth.longitude_deg -
        (east /
          (EARTH_RADIUS_M * Math.max(Math.cos(latitude / DEG_PER_RADIAN), 1e-6))) *
          DEG_PER_RADIAN;
      this.origin = { latitude, longitude };
    }

    nedToLatLng(position) {
      const north = Number(position?.[0]) || 0;
      const east = Number(position?.[1]) || 0;
      return L.latLng(
        this.origin.latitude + (north / EARTH_RADIUS_M) * DEG_PER_RADIAN,
        this.origin.longitude +
          (east /
            (EARTH_RADIUS_M *
              Math.max(Math.cos(this.origin.latitude / DEG_PER_RADIAN), 1e-6))) *
            DEG_PER_RADIAN,
      );
    }

    updateTracks(history) {
      const valid = history.filter(
        sample =>
          Array.isArray(sample.truth_position_ned_m) &&
          Array.isArray(sample.ins_position_ned_m),
      );
      this.truthTrack.setLatLngs(
        valid.map(sample => this.nedToLatLng(sample.truth_position_ned_m)),
      );
      this.insTrack.setLatLngs(
        valid.map(sample => this.nedToLatLng(sample.ins_position_ned_m)),
      );
      this.dvlTrack.setLatLngs(
        valid
          .filter(sample => sample.dvl_valid)
          .map(sample => this.nedToLatLng(sample.ins_position_ned_m)),
      );
    }

    updateMission(status) {
      this.missionLayer.clearLayers();
      const waypoints = status.mission?.waypoints_ned_m || [];
      if (!waypoints.length) return;
      const positions = waypoints.map(value => this.nedToLatLng(value));
      L.polyline(positions, {
        color: "#ffc857",
        weight: 2,
        opacity: 0.8,
        dashArray: "7 7",
      }).addTo(this.missionLayer);
      positions.forEach((position, index) => {
        L.marker(position, {
          icon: L.divIcon({
            className: "waypoint-icon",
            html: `<div class="waypoint-symbol"><span>${index + 1}</span></div>`,
            iconSize: [20, 20],
            iconAnchor: [10, 10],
          }),
        })
          .bindTooltip(
            `Waypoint ${index + 1}${
              index === status.mission.active_waypoint ? " · active" : ""
            }`,
          )
          .addTo(this.missionLayer);
      });
    }

    updateTransponders(status) {
      this.transponderLayer.clearLayers();
      const mode = status.simulation?.acoustic_mode;
      const values =
        mode === "lbl"
          ? status.transponders?.lbl || []
          : status.transponders?.iusbl
            ? [status.transponders.iusbl]
            : [];
      values.forEach((value, index) => {
        const label = mode === "lbl" ? `L${index + 1}` : "G";
        const name =
          mode === "lbl"
            ? `LBL transponder ${index + 1}`
            : "GPS/time transponder";
        L.marker(this.nedToLatLng(value), {
          icon: L.divIcon({
            className: "transponder-icon",
            html: `<div class="transponder-symbol">${label}</div>`,
            iconSize: [22, 22],
            iconAnchor: [11, 11],
          }),
        })
          .bindTooltip(name)
          .addTo(this.transponderLayer);
      });
    }

    updateUncertainty(status) {
      this.uncertaintyLayer.clearLayers();
      const position = status.ins?.position_ned_m;
      if (!position) return;
      const covariance = status.ins.horizontal_covariance_m2;
      let major = 2 * (Number(status.ins.horizontal_sigma_m) || 0);
      let minor = major;
      let angle = 0;
      if (
        Array.isArray(covariance) &&
        covariance.length === 2 &&
        covariance[0].length === 2
      ) {
        const a = Math.max(0, Number(covariance[0][0]) || 0);
        const b = Number(covariance[0][1]) || 0;
        const d = Math.max(0, Number(covariance[1][1]) || 0);
        const root = Math.sqrt((a - d) ** 2 + 4 * b * b);
        major = 2 * Math.sqrt(Math.max(0, (a + d + root) / 2));
        minor = 2 * Math.sqrt(Math.max(0, (a + d - root) / 2));
        angle = 0.5 * Math.atan2(2 * b, a - d);
      }
      const center = position;
      const points = [];
      for (let index = 0; index <= 48; index += 1) {
        const phase = (index / 48) * Math.PI * 2;
        const north =
          major * Math.cos(phase) * Math.cos(angle) -
          minor * Math.sin(phase) * Math.sin(angle);
        const east =
          major * Math.cos(phase) * Math.sin(angle) +
          minor * Math.sin(phase) * Math.cos(angle);
        points.push(
          this.nedToLatLng([center[0] + north, center[1] + east, center[2]]),
        );
      }
      L.polygon(points, {
        color: "#4ee4d2",
        fillColor: "#4ee4d2",
        fillOpacity: 0.09,
        opacity: 0.7,
        weight: 1,
      })
        .bindTooltip(
          `2σ uncertainty · ${major.toFixed(1)} × ${minor.toFixed(1)} m`,
        )
        .addTo(this.uncertaintyLayer);
    }

    updateAcousticFix(status) {
      const acoustic = status.acoustic;
      if (
        !acoustic?.valid ||
        !Array.isArray(acoustic.position_fix_ned_m) ||
        acoustic.timestamp_tai_ns === this.lastAcousticTimestamp
      ) {
        return;
      }
      if (acoustic.timestamp_tai_ns < this.lastAcousticTimestamp) {
        this.acousticFixLayer.clearLayers();
      }
      this.lastAcousticTimestamp = acoustic.timestamp_tai_ns;
      L.circleMarker(this.nedToLatLng(acoustic.position_fix_ned_m), {
        radius: 5,
        color: "#eadfff",
        fillColor: "#b48cff",
        fillOpacity: 0.9,
        weight: 1,
      })
        .bindTooltip(
          `${acoustic.mode.toUpperCase()} fix · ${Number(
            acoustic.range_m,
          ).toFixed(1)} m`,
        )
        .addTo(this.acousticFixLayer);
      const layers = this.acousticFixLayer.getLayers();
      if (layers.length > 120) {
        this.acousticFixLayer.removeLayer(layers[0]);
      }
    }

    update(status, history) {
      this.updateOrigin(status);
      this.updateTracks(history);
      this.updateMission(status);
      this.updateTransponders(status);
      this.updateUncertainty(status);
      this.updateAcousticFix(status);

      const insPosition = status.ins?.position_ned_m;
      const truthPosition = status.truth?.position_ned_m;
      if (!insPosition || !truthPosition) return;
      const vehicle = this.nedToLatLng(insPosition);
      this.vehicleMarker.setLatLng(vehicle);
      this.truthMarker.setLatLng(this.nedToLatLng(truthPosition));
      if (!this.initialMissionFit) {
        this.initialMissionFit = true;
        this.fitMission(false);
      }
      const arrow = this.vehicleMarker
        .getElement()
        ?.querySelector(".vehicle-arrow");
      if (arrow) {
        arrow.style.transform = `rotate(${Number(status.ins.heading_deg) || 0}deg)`;
      }
      document.getElementById("mapReadout").textContent =
        `${status.ins.latitude_deg.toFixed(6)}, ` +
        `${status.ins.longitude_deg.toFixed(6)} · ` +
        `${status.ins.depth_m.toFixed(1)} m`;
      if (this.follow) this.map.panTo(vehicle, { animate: false });
    }

    fitMission(userInitiated = true) {
      const layers = [
        ...this.missionLayer.getLayers(),
        this.insTrack,
        this.truthTrack,
      ];
      const group = L.featureGroup(layers);
      const bounds = group.getBounds();
      if (bounds.isValid()) {
        if (userInitiated) this.setFollow(false);
        this.map.fitBounds(bounds.pad(0.12), {
          maxZoom: 15,
          animate: false,
        });
      }
    }

    resetTracks() {
      this.truthTrack.setLatLngs([]);
      this.insTrack.setLatLngs([]);
      this.dvlTrack.setLatLngs([]);
      this.acousticFixLayer.clearLayers();
      this.lastAcousticTimestamp = -1;
    }

    invalidateSize() {
      this.map.invalidateSize(false);
    }
  }

  window.CoraNavigationMap = CoraNavigationMap;
})();
