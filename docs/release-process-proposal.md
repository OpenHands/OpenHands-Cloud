# OpenHands Enterprise Release Process Proposal

## Executive Summary

OpenHands Enterprise currently lacks a structured release process, creating
challenges in version management, customer support, and technical coordination
across multiple components. This proposal establishes a comprehensive release
strategy that balances rapid innovation with enterprise stability requirements.

**Key Recommendations:**

- **Weekly Enterprise releases staggered 1 week behind SaaS** for validation and
  stability
- **Support current + previous 2 releases** (3 total supported versions) during
  the rapid evolution phase
- **Unified versioning strategy** with synchronized version numbers across all
  components
- **Automated release coordination** to eliminate manual Git SHA tracking

**Expected Benefits:**

- Predictable release schedule for enterprise customers
- Reduced support burden through limited version support
- Improved quality assurance via SaaS validation period
- Streamlined technical coordination across repositories

This approach is designed for our current customer reality—early evaluation and
pilot phases—while establishing proper expectations for future production
deployments.

## Table of Contents

1. [Introduction](#1-introduction)
   - 1.1 [Problem Statement](#11-problem-statement)
   - 1.2 [Proposed Solution](#12-proposed-solution)
   - 1.3 [Additional Context](#13-additional-context)
     - 1.3.1 [Enterprise Customer Change
       Management](#131-enterprise-customer-change-management)
     - 1.3.2 [Current Customer Reality](#132-current-customer-reality)
     - 1.3.3 [Two Delivery Models](#133-two-delivery-models)
2. [Envisioned Future State](#2-envisioned-future-state)
   - 2.1 [Customer Experience](#21-customer-experience)
   - 2.2 [Release Cadence Options](#22-release-cadence-options)
3. [Technical Solution](#3-technical-solution)
   - 3.1 [Release Process: Synchronized Versions, Staggered Enterprise
     Release](#31-release-process-synchronized-versions-staggered-enterprise-release)
   - 3.2 [Repository Structure](#32-repository-structure)
   - 3.3 [Automation](#33-automation)
4. [Immediate Decisions](#4-immediate-decisions)
   - 4.1 [Product Classification](#41-product-classification)
   - 4.2 [Version Synchronization](#42-version-synchronization)
   - 4.3 [Release Relationship and Staggering](#43-release-relationship-and-staggering)
   - 4.4 [Release Support & Maintenance Policy](#44-release-support--maintenance-policy)

## 1. Introduction

This document proposes a structured release process and support policy for
OpenHands Enterprise, addressing current gaps in our release cadence, versioning
strategy, and customer support expectations.

### 1.1 Problem Statement

OpenHands Enterprise currently lacks:

1. **Established Release Process**: No defined release cadence or standardized
   process for OpenHands Enterprise (Helm charts)
2. **Technical Coordination Challenges**: Complex dependencies between multiple
   components:
   - Enterprise container (OpenHands core)
   - Runtime API container and Helm chart
   - Image loader container and chart
   - Runtimes container
3. **Version Management Issues**:
   - No clear tagging convention linking Helm chart releases to OpenHands core
     releases
   - Component versions are tracked via Git SHAs in workflow files, making them
     difficult to parse and understand
   - No readable mapping between SaaS releases and Enterprise releases
4. **Undefined Support Policy**: No established support policy for Enterprise
   customers, creating uncertainty around:
   - Which versions receive bug fixes and security updates
   - How long versions are supported
   - Customer upgrade expectations and timelines

### 1.2 Proposed Solution

#### 1.2.1 Establish Release Support Policy & Enterprise Release Cadence

**Recommended Policy**:

Over the next year the product will be evolving rapidly and customers will
largely be in evaluation, pilot and early production phases. During this period
we propose:

- **Enterprise releases weekly, staggered 1 week behind SaaS** to allow
  validation
- **Support current release + previous 2 releases** (3 total supported versions)
- **Current release**: Full bug fixes and security updates
- **Previous 2 releases**: Security fixes only
- **Older releases**: No support - customers must upgrade

This approach balances rapid delivery with enterprise stability needs while
limiting support burden. See Section 2.2 for alternative options and Section 3
for technical implementation details.

#### 1.2.2 Automate Release Process

**Unified Versioning Strategy**:

- **All components use identical semantic version numbers** (e.g., 0.73.0)
- **Synchronized releases** across all repositories and artifacts:
  - OpenHands Enterprise Helm chart: 0.73.0
  - Runtime API container & chart: 0.73.0
  - Image Loader container & chart: 0.73.0
  - Runtimes container: 0.73.0
- **Automated coordination** eliminates manual Git SHA tracking in workflow
  files
- **Single source of truth** for version compatibility

This replaces the current system of tracking component versions via Git SHAs in
workflow files with a clear, automated process where all components share the
same version number for each release.

### 1.3 Additional Context

#### 1.3.1 Enterprise Customer Change Management

Enterprise customers operate under different constraints than SaaS users, and
understanding these patterns is critical for designing an effective release
strategy.

**Two Classes of Enterprise Customers**:

**Traditional/Slow-Moving Enterprises**:

- **Update Frequency**: Semi-annual or quarterly updates only
- **Change Process**: Extensive corporate approval, change tickets, designated
  change windows
- **Industry Constraints**:
  - Insurance: November blackouts during open enrollment periods
  - Retail: Restricted changes during holiday shopping seasons
- **Support Expectations**: Long support windows, extensive backporting demands
- **Risk Profile**: Highly risk-averse, prefer stability over new features

**Forward-Leaning/Fast-Moving Enterprises**:

- **Update Frequency**: Weekly or monthly updates with proper change management
- **Change Process**: Streamlined approval processes, standard change windows
- **Infrastructure**: Cloud-native, containerized environments with image
  quarantine processes
- **Support Expectations**: Willing to stay current in exchange for high-quality
  releases
- **Risk Profile**: Accept faster pace if release quality is consistently high

**Note on Regulated Fast-Moving Enterprises**: Even highly regulated
organizations (financial services, healthcare, government contractors) can adopt
weekly release cadences when they have:

- **Standing weekly change windows** with pre-approved deployment processes
- **Automated image quarantine and scanning** in private container registries
- **Proven track record of stable releases** with minimal disruptive bugs
- **Rollback capabilities** and comprehensive monitoring

These organizations prioritize security and stability but recognize that staying
current with frequent, stable updates often provides better security posture
than running outdated versions with accumulated vulnerabilities.

#### 1.3.2 Current Customer Reality

**Important Note**: Our current Enterprise customers are in **early evaluation
and piloting phases**, not stable production deployments. However, as they
transition to production systems, they will either:

1. **Implement formal change management processes** with defined change windows
   and release schedules, or
2. **Silently fall behind on updates** and expect support on increasingly old
   versions

**We must establish our release and support policy now** to avoid future support
burden and set proper expectations before customers reach production scale.

#### 1.3.3 Two Delivery Models

We have currently been assuming model 1, but model 2 has precident for
enterprise customers streching back well over a decade. We need to decide which
to offer--not both!

**Note**: The remainder of this document assumes we continue with **Model 1 -
Customer-Managed Installations**, potentially streamlined with tools like
Replicated for easier deployment and update management. However, the **Model 2 -
Vendor-Managed Appliance Approach** is presented here as a viable alternative
that could fundamentally change our release strategy and support model.

**Model 1 - Customer-Managed Installations:**

- Customer controls their own upgrade timeline and process
- **Recommended Strategy**:
  - Push for weekly adoption with high-quality releases
  - Offer quarterly LTS releases as fallback option
  - **Limit support to maximum 1 quarter behind** (following [GitLab's
    maintenance policy](https://docs.gitlab.com/policy/maintenance/))
- **Success Factor**: Consistent, stable, high-quality releases that build
  customer confidence

**Model 2 - Vendor-Managed Appliance Approach:**

- On-premises deployment but vendor-controlled update process
- Pre-agreed change processes with standard cadences
- Enables weekly standing change windows
- Reduces customer change management overhead

**Critical Success Factor:**

**The key to keeping customers current is delivering stable, high-quality
releases consistently.** If we fail to maintain release quality:

- Customers will lose confidence and resist frequent updates
- We'll face increased demands for backporting fixes to old versions
- Customer support costs will escalate significantly
- Engineering productivity will suffer from extensive backporting work

This reinforces why the proposed 1-week stagger between SaaS and Enterprise
releases is valuable—it provides a validation period to ensure Enterprise
customers receive proven, stable releases.

## 2. Envisioned Future State

### 2.1 Customer Experience

#### Enterprise Customers

- **Predictable Release Schedule**: Clear expectations for when new versions are
  available
- **Transparent Version Mapping**: Easy understanding of how Enterprise versions
  relate to SaaS releases
- **Defined Support Windows**: Clear knowledge of which versions receive
  security updates and for how long
- **Flexible Adoption Options**: Choice between staying current with frequent
  updates or using Long-Term Support (LTS) releases

#### Internal Teams

- **Automated Release Coordination**: Streamlined process for releasing
  coordinated component versions
- **Reduced Support Burden**: Clear policies limiting the number of supported
  versions
- **Improved Quality Assurance**: Staggered releases allowing SaaS validation
  before Enterprise deployment

### 2.2 Release Cadence Options

#### Option 1 - Weekly Releases with Stagger

- SaaS releases weekly (current cadence)
- Enterprise releases weekly, staggered by 1 week behind SaaS
- Provides validation period while maintaining rapid delivery

#### Option 2 - Current Cadence no Stagger

- Enterprise releases match SaaS cadence
- Simpler implementation but higher risk to enterprise client stability

#### Option 3 - Quarterly LTS Release

We propose deferring this until custoers are have shifted to traditional
production change management and ask us for additional support options, but we
could start here.

- **Monthly Release Cadence**: Transition to monthly Enterprise releases with
  continuous SaaS beta
- **Quarterly LTS Option**: Long-term support releases with security-only fixes

Support:

- **Current Release**: Full bug fix and security support
- **Previous 2 Releases**: Security fixes only
- **Older Releases**: No support (customers must upgrade)

This follows [GitLab's model](https://docs.gitlab.com/policy/maintenance/).

## 3. Technical Solution

### 3.1 Release Process: Synchronized Versions, Staggered Enterprise Release

**Process Overview:**

- OSS Release 0.73.0 triggers immediate SaaS release
- Enterprise Release 0.73.0 follows 1 week later (no beta distinction)
- Release automation creates charts for the prior OSS version to maintain
  compatibility

**Technical Implementation:**

**Release Automation:**

When OSS 0.73.0 is tagged, three release processes are triggered:

1. **OSS Release**:
   - `ghcr.io/all-hands-ai/openhands` container tagged and published with 0.73.0
   - `ghcr.io/all-hands-ai/runtime` container built and tagged with 0.73.0

2. **SaaS Release**:
   - `ghcr.io/all-hands-ai/enterprise-server` container built and published with
     0.73.0
   - `ghcr.io/all-hands-ai/runtime-api` container built and published with
     0.73.0

3. **Enterprise Release**:
   - **Publishes prior week's charts**: Charts for 0.72.0 (previous version) are
     published to `ghcr.io/all-hands-ai/helm-charts`
   - **Prepares next week's charts**: Chart.yaml files updated to version 0.73.0
     and appVersion to 0.73.0
   - **Schedules for next week**: Charts for 0.73.0 will be published in 1 week
     after SaaS validation

#### 3.1.1 Scenario: Issue Found in SaaS Release During Stagger Period

1. Release SaaS with new patch number: 0.73.1
2. Run Enterprise chart staging so that that unpublished Enterprise Charts will
   release 0.73.1 instead of 0.73.0.

#### 3.1.2 Scenario: Faster than Weekly OSS / SaaS Release Cadance

1. OSS and SaaS Release 0.73.0 and 0.74.0 in the same week
2. Run Enterprise chart staging so that that unpublished Enterprise Charts will
   release 0.74.0 instead of 0.73.0.

#### 3.1.3 Scenario: Fix For Helm Charts, Not Code

1. OSS and SaaS Release 0.73.0
2. A week later Helm Charts are published
3. Bug found in Helm Chart, not containers.
4. Release helm chart as 0.73.0-fix1

### 3.2 Repository Structure

#### 3.2.1 Current State

Containers:

**All-Hands-AI/OpenHands (OSS):**

- `ghcr.io/all-hands-ai/openhands` (OSS)
- `ghcr.io/all-hands-ai/runtime` (OSS)
- `ghcr.io/all-hands-ai/enterprise-server` (OHE, SaaS)

**All-Hands-AI/runtime-api:**

- `ghcr.io/all-hands-ai/runtime-api` (OHE, SaaS)

Charts:

**All-Hands-AI/OpenHands-Cloud (OHE):**

- `enterprise-server` - Main OpenHands Enterprise chart
- `runtime-api` - Runtime API service chart  
- `image-loader` - Image loading daemonset chart (uses
  `ghcr.io/all-hands-ai/runtime` from OSS)

**All-Hands-AI/deploy (SaaS):**

- `data-platform` - Data platform service chart
- `error-page` - Error page service chart
- `image-loader` - Image loading daemonset chart (uses
  `ghcr.io/all-hands-ai/runtime` from OSS)
- `keycloak` - Keycloak identity service chart
- `openhands` - OpenHands SaaS chart

#### 3.2.2 Future State

TBD

### 3.3 Automation

TBD

## 4. Immediate Decisions

This section outlines the key decision points that need to be resolved to move
forward with the proposed release process.

### 4.1 Product Classification

**Decision Required**: Agree that OpenHands Enterprise (OHE) is a product and
needs to be treated as a first-class product in our release approach.

**Rationale**: This establishes the foundation for all subsequent decisions
about versioning, automation, and support policies. Treating OHE as a
first-class product ensures it receives appropriate engineering resources and
process attention.

### 4.2 Version Synchronization

**Decision Required**: Agree that version numbers need to line up across all
components and that we need to put work into automation to achieve that
synchronization.

**Timing Consideration**: This work could be implemented after some upcoming
repository migrations or after v1 lands, depending on current engineering
priorities.

**Technical Scope**: 
- All components use identical semantic version numbers (e.g., 0.73.0)
- Automated coordination eliminates manual Git SHA tracking in workflow files
- Single source of truth for version compatibility

### 4.3 Release Relationship and Staggering

**Decision Required**: Agree about the OpenHands Enterprise relationship to SaaS
releases and the stagger approach.

**Proposed Approach**:
- Enterprise releases weekly, staggered 1 week behind SaaS releases
- Provides validation period for enterprise stability
- Maintains rapid delivery while reducing risk

### 4.4 Release Support & Maintenance Policy

**Decision Required**: Introduce the need for a support policy, even if we don't
fully define all details immediately.

**Key Questions to Address**:
- What versions are considered supported?
- What updates do we continue to make to each supported version?
- How long are versions supported before requiring customer upgrades?

**Proposed Initial Framework**:
- **Current release**: Full bug fixes and security updates
- **Previous 2 releases**: Security fixes only  
- **Older releases**: No support - customers must upgrade
- **Total supported versions**: 3 (current + 2 previous)

**Note**: This policy can be refined based on customer feedback and operational
experience, but establishing the framework now sets proper expectations.
