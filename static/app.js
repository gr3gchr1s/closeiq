"use strict";

// CloseIQ static frontend (Phase 1B, Workstream C).
//
// Security rule: every value that came from the API or a CSV file is
// untrusted. It is inserted into the page only via textContent,
// createElement, and other DOM APIs — never innerHTML, outerHTML,
// insertAdjacentHTML, or an inline event-handler string. See
// docs/phase_1b_spec.md, section 4 (WS-C).

(function () {
  async function safeJson(response) {
    try {
      return await response.json();
    } catch (error) {
      return null;
    }
  }

  function describeErrorDetail(detail) {
    if (typeof detail === "string" && detail) {
      return detail;
    }

    if (detail && typeof detail === "object") {
      if (typeof detail.message === "string" && detail.message) {
        return detail.message;
      }

      if (detail.reason === "unparseable_dates") {
        return (
          "One or more rows have a missing or invalid date. Please check " +
          "your files and try again."
        );
      }

      if (typeof detail.reason === "string" && detail.reason) {
        return detail.reason.replace(/_/g, " ");
      }
    }

    return "The upload could not be processed. Please check your files and try again.";
  }

  // ---------------------------------------------------------------------
  // Upload page (index.html)
  // ---------------------------------------------------------------------

  function initUploadPage() {
    const form = document.getElementById("upload-form");
    const journalInput = document.getElementById("journal-file");
    const bankInput = document.getElementById("bank-file");
    const periodSection = document.getElementById("period-section");
    const periodSelect = document.getElementById("period-select");
    const statusArea = document.getElementById("status-area");
    const statusMessage = document.getElementById("status-message");
    const submitButton = document.getElementById("submit-button");

    if (!form) {
      return;
    }

    function clearStatus() {
      statusArea.hidden = true;
      statusArea.classList.remove("status-error", "status-info");
      statusMessage.textContent = "";
    }

    function setStatus(message, kind) {
      statusMessage.textContent = message;
      statusArea.hidden = false;
      statusArea.classList.remove("status-error", "status-info");
      statusArea.classList.add(kind === "info" ? "status-info" : "status-error");
    }

    function setBusy(isBusy) {
      submitButton.disabled = isBusy;
      submitButton.textContent = isBusy ? "Running…" : "Run Close Review";
    }

    function showPeriodPicker(months) {
      periodSelect.replaceChildren();

      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.textContent = "Select the close period";
      periodSelect.appendChild(placeholder);

      (Array.isArray(months) ? months : []).forEach((month) => {
        const option = document.createElement("option");
        option.value = String(month);
        option.textContent = String(month);
        periodSelect.appendChild(option);
      });

      periodSection.hidden = false;
      setStatus(
        "Your files span more than one calendar month. Choose the " +
          "correct close period below and submit again.",
        "info"
      );
    }

    async function submitUpload() {
      if (!journalInput.files[0] || !bankInput.files[0]) {
        setStatus("Choose both a journal file and a bank file.", "error");
        return;
      }

      clearStatus();
      setBusy(true);

      const formData = new FormData();
      formData.append("journal_file", journalInput.files[0]);
      formData.append("bank_file", bankInput.files[0]);
      if (periodSelect.value) {
        formData.append("close_period", periodSelect.value);
      }

      let response;
      try {
        response = await fetch("/close-runs", {
          method: "POST",
          body: formData,
        });
      } catch (networkError) {
        setStatus(
          "Could not reach the server. Check your connection and try again.",
          "error"
        );
        setBusy(false);
        return;
      }

      if (response.status === 202) {
        const body = await safeJson(response);
        if (body && body.job_id) {
          window.location.href = "/jobs/" + encodeURIComponent(body.job_id);
          return;
        }
        setStatus(
          "The server accepted the upload but returned an unexpected " +
            "response. Please try again.",
          "error"
        );
        setBusy(false);
        return;
      }

      if (response.status === 422) {
        const body = await safeJson(response);
        const detail = body ? body.detail : null;

        if (
          detail &&
          typeof detail === "object" &&
          detail.reason === "period_ambiguous"
        ) {
          showPeriodPicker(detail.months);
          setBusy(false);
          return;
        }

        setStatus(describeErrorDetail(detail), "error");
        setBusy(false);
        return;
      }

      setStatus(
        "The server returned an unexpected error (status " +
          response.status +
          "). Please try again.",
        "error"
      );
      setBusy(false);
    }

    form.addEventListener("submit", function (event) {
      event.preventDefault();
      submitUpload();
    });
  }

  // ---------------------------------------------------------------------
  // Job/results page (jobs.html)
  // ---------------------------------------------------------------------

  const POLL_INTERVAL_MS = 2000;

  function getJobIdFromPath() {
    const segments = window.location.pathname.split("/").filter(Boolean);
    if (!segments.length) {
      return "";
    }
    try {
      return decodeURIComponent(segments[segments.length - 1]);
    } catch (error) {
      return segments[segments.length - 1];
    }
  }

  function initJobsPage() {
    const processingSection = document.getElementById("processing-section");
    const processingMessage = document.getElementById("processing-message");
    const failedSection = document.getElementById("failed-section");
    const failedMessage = document.getElementById("failed-message");
    const notFoundSection = document.getElementById("not-found-section");
    const resultsSection = document.getElementById("results-section");
    const periodValue = document.getElementById("period-value");
    const summaryCards = document.getElementById("summary-cards");
    const exceptionsBody = document.getElementById("exceptions-body");

    if (!processingSection) {
      return;
    }

    function hideAllSections() {
      processingSection.hidden = true;
      failedSection.hidden = true;
      notFoundSection.hidden = true;
      resultsSection.hidden = true;
    }

    function setProcessingText(text) {
      hideAllSections();
      processingSection.hidden = false;
      processingMessage.textContent = text;
    }

    function showNotFound() {
      hideAllSections();
      notFoundSection.hidden = false;
    }

    function showFailed(errorDetail) {
      hideAllSections();
      failedSection.hidden = false;
      failedMessage.textContent =
        typeof errorDetail === "string" && errorDetail
          ? errorDetail
          : "The close review could not be completed.";
    }

    function makeCell(value, label, extraClassName) {
      const cell = document.createElement("td");
      cell.setAttribute("data-label", label);
      if (extraClassName) {
        cell.className = extraClassName;
      }
      cell.textContent = value === null || value === undefined ? "" : String(value);
      return cell;
    }

    function formatSourceIds(sourceIds) {
      if (!Array.isArray(sourceIds)) {
        return "";
      }
      return sourceIds.map(String).join(", ");
    }

    // Never renders the raw evidence object: only a small, known set of
    // display-safe fields (reason, difference, external_reference), all
    // inserted via textContent later in makeCell. Falls back to a fixed
    // string if none of those fields are present.
    function describeEvidence(evidence) {
      if (!evidence || typeof evidence !== "object") {
        return "Additional evidence available";
      }

      const parts = [];

      if (typeof evidence.reason === "string" && evidence.reason) {
        parts.push(evidence.reason);
      }

      if (
        typeof evidence.difference === "string" ||
        typeof evidence.difference === "number"
      ) {
        parts.push("Difference: " + evidence.difference);
      }

      if (
        typeof evidence.external_reference === "string" &&
        evidence.external_reference
      ) {
        parts.push("Ref: " + evidence.external_reference);
      }

      if (!parts.length) {
        return "Additional evidence available";
      }

      return parts.join(" — ");
    }

    function formatAmount(evidence) {
      if (!evidence || typeof evidence !== "object") {
        return "";
      }
      if (typeof evidence.amount === "string" || typeof evidence.amount === "number") {
        return String(evidence.amount);
      }
      return "";
    }

    function severityClassName(severity) {
      if (severity === "high") return "severity-high";
      if (severity === "medium") return "severity-medium";
      if (severity === "low") return "severity-low";
      return "";
    }

    function renderSummaryCards(summary) {
      summaryCards.replaceChildren();

      const fields = [
        ["total", "Total"],
        ["open", "Open"],
        ["reviewed", "Reviewed"],
        ["resolved", "Resolved"],
        ["dismissed", "Dismissed"],
      ];

      fields.forEach(function (entry) {
        const key = entry[0];
        const label = entry[1];

        const card = document.createElement("div");
        card.className = "summary-card";

        const value = document.createElement("div");
        value.className = "summary-card-value";
        value.textContent = String((summary && summary[key]) || 0);

        const caption = document.createElement("div");
        caption.className = "summary-card-label";
        caption.textContent = label;

        card.appendChild(value);
        card.appendChild(caption);
        summaryCards.appendChild(card);
      });
    }

    function renderExceptionsTable(exceptions) {
      exceptionsBody.replaceChildren();

      const rows = Array.isArray(exceptions) ? exceptions : [];

      if (!rows.length) {
        const row = document.createElement("tr");
        const cell = document.createElement("td");
        cell.colSpan = 6;
        cell.textContent = "No open exceptions for this close.";
        row.appendChild(cell);
        exceptionsBody.appendChild(row);
        return;
      }

      rows.forEach(function (exception) {
        const row = document.createElement("tr");

        row.appendChild(makeCell(exception.exception_id, "Exception ID"));
        row.appendChild(makeCell(exception.exception_type, "Type"));
        row.appendChild(
          makeCell(
            exception.severity,
            "Severity",
            severityClassName(exception.severity)
          )
        );
        row.appendChild(
          makeCell(formatSourceIds(exception.source_ids), "Source IDs")
        );
        row.appendChild(makeCell(describeEvidence(exception.evidence), "Reason"));
        row.appendChild(makeCell(formatAmount(exception.evidence), "Amount"));

        exceptionsBody.appendChild(row);
      });
    }

    function renderResults(job, summary, exceptions) {
      hideAllSections();
      resultsSection.hidden = false;

      periodValue.textContent = job.close_period || "";

      renderSummaryCards(summary);
      renderExceptionsTable(exceptions);
    }

    async function showResults(job) {
      let summaryResponse;
      let exceptionsResponse;

      try {
        const responses = await Promise.all([
          fetch(
            "/close-runs/" +
              encodeURIComponent(job.close_run_id) +
              "/close-summary"
          ),
          fetch(
            "/close-runs/" +
              encodeURIComponent(job.close_run_id) +
              "/exceptions"
          ),
        ]);
        summaryResponse = responses[0];
        exceptionsResponse = responses[1];
      } catch (networkError) {
        hideAllSections();
        failedSection.hidden = false;
        failedMessage.textContent =
          "Could not reach the server while loading the results.";
        return;
      }

      if (summaryResponse.status === 404 || exceptionsResponse.status === 404) {
        showNotFound();
        return;
      }

      if (!summaryResponse.ok || !exceptionsResponse.ok) {
        hideAllSections();
        failedSection.hidden = false;
        failedMessage.textContent = "Could not load the results for this close run.";
        return;
      }

      const summary = await safeJson(summaryResponse);
      const exceptions = await safeJson(exceptionsResponse);

      if (!summary || !exceptions) {
        hideAllSections();
        failedSection.hidden = false;
        failedMessage.textContent = "The server returned an unexpected response.";
        return;
      }

      renderResults(job, summary, exceptions);
    }

    async function pollJob(jobId) {
      let response;
      try {
        response = await fetch("/close-jobs/" + encodeURIComponent(jobId));
      } catch (networkError) {
        setProcessingText("Could not reach the server. Retrying…");
        window.setTimeout(function () {
          pollJob(jobId);
        }, POLL_INTERVAL_MS);
        return;
      }

      if (response.status === 404) {
        showNotFound();
        return;
      }

      if (!response.ok) {
        setProcessingText(
          "Unexpected server response (status " +
            response.status +
            "). Retrying…"
        );
        window.setTimeout(function () {
          pollJob(jobId);
        }, POLL_INTERVAL_MS);
        return;
      }

      const job = await safeJson(response);

      if (!job) {
        setProcessingText("Unexpected response from the server. Retrying…");
        window.setTimeout(function () {
          pollJob(jobId);
        }, POLL_INTERVAL_MS);
        return;
      }

      if (job.status === "queued") {
        setProcessingText("Waiting to start…");
        window.setTimeout(function () {
          pollJob(jobId);
        }, POLL_INTERVAL_MS);
        return;
      }

      if (job.status === "running") {
        setProcessingText("Running the close review…");
        window.setTimeout(function () {
          pollJob(jobId);
        }, POLL_INTERVAL_MS);
        return;
      }

      if (job.status === "failed") {
        showFailed(job.error_detail);
        return;
      }

      if (job.status === "succeeded") {
        await showResults(job);
        return;
      }

      // Unknown status: keep polling rather than getting stuck.
      setProcessingText("Waiting for the close review to finish…");
      window.setTimeout(function () {
        pollJob(jobId);
      }, POLL_INTERVAL_MS);
    }

    const jobId = getJobIdFromPath();
    if (!jobId) {
      showNotFound();
      return;
    }

    pollJob(jobId);
  }

  // ---------------------------------------------------------------------

  document.addEventListener("DOMContentLoaded", function () {
    const page = document.body.getAttribute("data-page");
    if (page === "upload") {
      initUploadPage();
    } else if (page === "jobs") {
      initJobsPage();
    }
  });
})();
